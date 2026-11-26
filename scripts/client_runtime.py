"""Residential-only snapshots, local recovery, and application-level failover."""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.deps'))
import requests  # noqa: E402

from scripts.child_job import ChildJob  # noqa: E402

CLIENT = ROOT / '.private/client'
STATE = CLIENT / 'supervisor.json'
STOP = CLIENT / 'stop.request'
DISABLED = CLIENT / 'disabled'
REFRESH = CLIENT / 'refresh.request'
PORT, CONTROLLER = 17898, 19091
CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def direct_session():
    session = requests.Session()
    session.trust_env = False
    return session


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def write_state(**state):
    atomic_json(STATE, {**state, 'updated_at': datetime.now(timezone.utc).isoformat()})


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError('Missing timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')
    return parsed.timestamp()


def validate_snapshot(source, deployment, now=None):
    now = time.time() if now is None else now
    meta = source.get('x-gate', {})
    created, expires = parse_time(meta['generated_at']), parse_time(meta['expires_at'])
    if created > now + 60 or expires <= now or expires <= created or now - created > 7200 or expires - created > 7201:
        raise ValueError('Snapshot is expired or has invalid freshness')
    strict = deployment.get('residential_only', True)
    if strict and meta.get('mode') != 'residential-candidates':
        raise ValueError('Mixed or unknown node pool is forbidden')
    nodes = meta.get('nodes', {})
    if not source.get('proxies'):
        raise ValueError('No residential candidates')
    for proxy in source['proxies']:
        node = nodes.get(proxy['name'], {})
        if strict and (node.get('candidate') is not True or node.get('is_datacenter') is not False or
                       node.get('is_abuser') is True or not node.get('org')):
            raise ValueError('Unqualified residential candidate')
        if not ipaddress.ip_address(node['exit_ip']).is_global:
            raise ValueError('Exit must be a public IP')
        if proxy.get('type') != 'vless' or proxy.get('server') != deployment['edge_host'] or \
                proxy.get('servername') != deployment['edge_host'] or proxy.get('tls') is not True or \
                proxy.get('skip-cert-verify') is not False:
            raise ValueError('Unexpected tunnel or TLS settings')
        if proxy.get('ws-opts', {}).get('path') != f"/sstp://vpn:vpn@{node['host']}:{node['port']}":
            raise ValueError('Node metadata does not match tunnel path')
    return source


def isolate_config(source, secret):
    proxies = source.get('proxies', [])
    if not isinstance(proxies, list) or not proxies:
        raise ValueError('No usable proxies in subscription')
    names = [p['name'] for p in proxies]
    return {
        'mixed-port': PORT, 'allow-lan': False, 'bind-address': '127.0.0.1',
        'external-controller': f'127.0.0.1:{CONTROLLER}', 'secret': secret,
        'mode': 'rule', 'log-level': 'warning', 'ipv6': False,
        'tun': {'enable': False}, 'dns': {'enable': False},
        'profile': {'store-selected': False, 'store-fake-ip': False},
        'proxies': proxies, 'proxy-groups': [
            {'name': 'GATE', 'type': 'select', 'proxies': ['GATE-FALLBACK', *names, 'REJECT']},
            {'name': 'GATE-FALLBACK', 'type': 'fallback', 'proxies': names,
             'url': 'https://www.gstatic.com/generate_204', 'interval': 120, 'timeout': 12000, 'lazy': False},
        ], 'rules': ['MATCH,GATE'],
    }


def blocked_config(secret):
    config = isolate_config({'proxies': [{'name': 'placeholder'}]}, secret)
    config['proxies'] = []
    config['proxy-groups'] = [{'name': 'GATE', 'type': 'select', 'proxies': ['REJECT']}]
    return config


def build_config(source, deployment):
    if source is None:
        return blocked_config(deployment['controller_secret'])
    source = validate_snapshot(source, deployment)
    config = isolate_config(source, deployment['controller_secret'])
    preferred = deployment.get('preferred_hosts', [])
    def rank(name):
        host = source['x-gate']['nodes'][name]['host']
        return preferred.index(host) if host in preferred else len(preferred)
    config['proxy-groups'][1]['proxies'].sort(key=rank)
    return config


def save_config(source, deployment):
    config = build_config(source, deployment)
    core_home = Path(deployment.get('client_runtime', str(CLIENT)))
    for directory in {CLIENT, core_home}:
        atomic_json(directory / 'config.json', config)
    return core_home


def control(http, deployment, method, path, **kwargs):
    response = http.request(method, f'http://127.0.0.1:{CONTROLLER}' + path,
                            headers={'Authorization': 'Bearer ' + deployment['controller_secret']},
                            timeout=5, **kwargs)
    response.raise_for_status()
    return response.json() if response.content else None


def fetch_snapshot(http, deployment):
    response = http.get('https://' + deployment['edge_host'] + '/subscription/clash',
                        headers={'Authorization': 'Bearer ' + deployment['sub_token']}, timeout=(5, 15))
    response.raise_for_status()
    return validate_snapshot(response.json(), deployment)


def retry_delay(failures):
    return min(300, 15 * 2 ** min(max(failures - 1, 0), 5))


class Failover:
    def __init__(self, names):
        self.names = names
        self.cooldown = {}
        self.failures = 0
        self.switches = 0

    def failed(self, name, now):
        self.cooldown[name] = now + 180

    def candidates(self, current, now):
        return [n for n in self.names if n != current and self.cooldown.get(n, 0) <= now]


def matching_node(old_source, new_source, selected):
    old_node = (old_source or {}).get('x-gate', {}).get('nodes', {}).get(selected)
    if not old_node:
        return None
    for name, node in new_source['x-gate']['nodes'].items():
        if all(node.get(k) == old_node.get(k) for k in ('host', 'port', 'exit_ip')):
            return name
    return None


def probe_exit(expected_ip):
    # Never reuse a keepalive connection after switching a proxy group.
    with direct_session() as http:
        response = http.get('https://www.cloudflare.com/cdn-cgi/trace',
                            proxies={'https': f'http://127.0.0.1:{PORT}'},
                            headers={'Connection': 'close'}, timeout=(5, 12))
        response.raise_for_status()
        trace = dict(line.split('=', 1) for line in response.text.splitlines() if '=' in line)
        if trace.get('ip') != expected_ip:
            raise ValueError('Actual exit changed; residential classification must be renewed')
        return trace['ip']


def check_ports():
    for port in (PORT, CONTROLLER):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))


def acquire_lock():
    handle = (CLIENT / 'supervisor.lock').open('a+b')
    handle.seek(0, 2)
    if not handle.tell():
        handle.write(b'0')
        handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def supervise(scheduled=False):
    CLIENT.mkdir(parents=True, exist_ok=True)
    if scheduled and DISABLED.exists():
        return
    lock = acquire_lock()
    if lock is None:
        return
    child = scan = None
    job = None
    scan_log = core_log = None
    try:
        check_ports()  # Do not replace any unrelated listener.
        job = ChildJob()
        STOP.unlink(missing_ok=True)
        deployment = json.loads((ROOT / '.private/deployment.json').read_text(encoding='utf-8'))
        executable = Path(deployment['client_executable'])
        http = direct_session()
        source = None
        try:
            source = validate_snapshot(json.loads((CLIENT / 'subscription.json').read_text(encoding='utf-8')), deployment)
        except (OSError, ValueError, KeyError, TypeError):
            pass
        state = {'status': 'starting', 'supervisor_pid': os.getpid(), 'proxy': f'http://127.0.0.1:{PORT}',
                 'mode': 'residential-candidates', 'nodes': 0, 'switches': 0}
        next_refresh = next_probe = next_restart = next_scan = 0
        refresh_failures = crashes = 0
        selector = Failover([])
        selected = None
        last_state = None
        core_log = (CLIENT / 'core.log').open('a', encoding='utf-8')
        while not STOP.exists() and not DISABLED.exists():
            now = time.monotonic()
            if scan is not None and scan.poll() is not None:
                state['local_scan_exit'] = scan.returncode
                if scan.returncode:
                    next_scan = min(next_scan, now + 300)
                scan = None
                scan_log.close()
                scan_log = None
                next_refresh = 0
            if REFRESH.exists() or now >= next_refresh:
                REFRESH.unlink(missing_ok=True)
                try:
                    fresh = fetch_snapshot(http, deployment)
                    changed = source is None or fresh['x-gate']['generated_at'] != source['x-gate']['generated_at']
                    if changed:
                        retained = matching_node(source, fresh, selected)
                        core_home = save_config(fresh, deployment)
                        if child is not None and child.poll() is None:
                            control(http, deployment, 'PUT', '/configs?force=true', json={'path': str(core_home / 'config.json')})
                            if retained:
                                control(http, deployment, 'PUT', '/proxies/GATE', json={'name': retained})
                        source = fresh
                        atomic_json(CLIENT / 'subscription.json', fresh)
                        switch_count = selector.switches
                        selector = Failover(build_config(source, deployment)['proxy-groups'][1]['proxies'])
                        selector.switches = switch_count
                        selected = retained
                        next_probe = 0
                    refresh_failures = 0
                    next_refresh = now + 600
                    state.update(last_refresh=datetime.now(timezone.utc).isoformat(), nodes=len(source['proxies']),
                                 expires_at=source['x-gate']['expires_at'])
                    state.pop('refresh_error', None)
                    state.pop('retry_in_seconds', None)
                except Exception as error:
                    refresh_failures += 1
                    next_refresh = now + retry_delay(refresh_failures)
                    state.update(refresh_error=type(error).__name__, retry_in_seconds=retry_delay(refresh_failures))
            fresh_enough = False
            if source is not None:
                try:
                    validate_snapshot(source, deployment)
                    fresh_enough = True
                except (ValueError, KeyError, TypeError):
                    source = None
                    selected = None
                    state.update(status='waiting_for_residential_candidates', nodes=0)
                    core_home = save_config(None, deployment)
                    if child is not None and child.poll() is None:
                        control(http, deployment, 'PUT', '/configs?force=true', json={'path': str(core_home / 'config.json')})
            needs_scan = source is None or time.time() - parse_time(source['x-gate']['generated_at']) > 1800
            if needs_scan and scan is None and now >= next_scan:
                scan_log = (CLIENT / 'local-refresh.log').open('a', encoding='utf-8')
                scan = subprocess.Popen([sys.executable, str(ROOT / 'scripts/refresh_local.py')], cwd=ROOT,
                                        stdout=scan_log, stderr=subprocess.STDOUT, creationflags=CREATE_FLAGS)
                job.add(scan)
                next_scan = now + 1800
                state['local_scan_started_at'] = datetime.now(timezone.utc).isoformat()
            if child is not None and child.poll() is not None:
                child = None
                crashes += 1
                next_restart = now + retry_delay(crashes)
                state.update(status='restarting', restart_count=crashes)
            if child is None and now >= next_restart:
                core_home = save_config(source, deployment)
                child = subprocess.Popen([str(executable), '-d', str(core_home), '-f', str(core_home / 'config.json')],
                                         stdout=core_log, stderr=subprocess.STDOUT, creationflags=CREATE_FLAGS)
                job.add(child)
                state['client_pid'] = child.pid
                selected = None
                next_probe = now + 3
                if source is not None:
                    switch_count = selector.switches
                    selector = Failover(build_config(source, deployment)['proxy-groups'][1]['proxies'])
                    selector.switches = switch_count
            if child is not None and child.poll() is None and now >= next_probe and fresh_enough:
                nodes = source['x-gate']['nodes']
                try:
                    if selected is None:
                        selected = selector.names[0]
                        control(http, deployment, 'PUT', '/proxies/GATE', json={'name': selected})
                    actual = probe_exit(nodes[selected]['exit_ip'])
                    selector.failures = 0
                    state.update(status='running', selected=selected, exit_ip=actual,
                                 last_verified_at=datetime.now(timezone.utc).isoformat())
                    state.pop('probe_error', None)
                except Exception as error:
                    selector.failures += 1
                    state.update(status='checking_exit', probe_error=type(error).__name__)
                    if selector.failures >= 2 or selected == 'REJECT':
                        previous = selected
                        selector.failed(previous, now)
                        found = False
                        for name in selector.candidates(previous, now)[:4]:
                            if STOP.exists():
                                break
                            try:
                                control(http, deployment, 'PUT', '/proxies/GATE', json={'name': name})
                                actual = probe_exit(nodes[name]['exit_ip'])
                                selected = name
                                selector.failures = 0
                                selector.switches += 1
                                state.update(status='running', selected=name, exit_ip=actual, switches=selector.switches,
                                             last_switch_at=datetime.now(timezone.utc).isoformat(),
                                             last_verified_at=datetime.now(timezone.utc).isoformat())
                                state.pop('probe_error', None)
                                found = True
                                break
                            except Exception:
                                selector.failed(name, now)
                        if not found:
                            selected = 'REJECT'
                            control(http, deployment, 'PUT', '/proxies/GATE', json={'name': 'REJECT'})
                            state.update(status='no_working_residential_exit', selected='REJECT')
                next_probe = time.monotonic() + 30
            elif not fresh_enough:
                state['status'] = 'waiting_for_residential_candidates'
            if state != last_state:
                write_state(**state)
                last_state = state.copy()
            time.sleep(1)
        state['status'] = 'stopped'
        write_state(**state)
    finally:
        for process in [scan, child]:
            if process is not None and process.poll() is None:
                process.terminate()  # Only the child handle created above.
                process.wait(timeout=10)
        if job is not None:
            job.close()
        for handle in [scan_log, core_log, lock]:
            if handle:
                handle.close()
        STOP.unlink(missing_ok=True)
