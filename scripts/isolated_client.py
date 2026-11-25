"""Own only the child process and loopback ports created by this project."""
from __future__ import annotations

import argparse
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

CLIENT = ROOT / '.private' / 'client'
STATE = CLIENT / 'supervisor.json'
STOP = CLIENT / 'stop.request'
REFRESH = CLIENT / 'refresh.request'
PORT = 17898
CONTROLLER = 19091


def isolate_config(source: dict, secret: str) -> dict:
    if not isinstance(source.get('proxies'), list) or not source['proxies']:
        raise ValueError('No usable proxies in subscription')
    # Build a small allowlist, rather than accepting remote process/listener options.
    return {
        'mixed-port': PORT, 'allow-lan': False, 'bind-address': '127.0.0.1',
        'external-controller': f'127.0.0.1:{CONTROLLER}', 'secret': secret,
        'mode': 'rule', 'log-level': 'warning', 'ipv6': False,
        'tun': {'enable': False}, 'dns': {'enable': False},
        'profile': {'store-selected': False, 'store-fake-ip': False},
        'proxies': source['proxies'], 'proxy-groups': source.get('proxy-groups', []),
        'rules': ['MATCH,GATE'],
    }


def direct_session():
    session = requests.Session()
    session.trust_env = False
    return session


def write_state(**state):
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, STATE)


def fetch_config(session, deployment):
    response = session.get('https://' + deployment['edge_host'] + '/subscription/clash',
                           headers={'Authorization': 'Bearer ' + deployment['sub_token']}, timeout=25)
    response.raise_for_status()
    config = isolate_config(response.json(), deployment['controller_secret'])
    if any(p.get('servername') != deployment['edge_host'] for p in config['proxies']):
        raise ValueError('Unexpected subscription TLS server name')
    # A protected workspace can reject a new unsigned native executable. Keep
    # the native runtime in the user's application directory without weakening
    # Windows folder protection; Python still owns the project control files.
    directories = {CLIENT, Path(deployment.get('client_runtime', str(CLIENT)))}
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / 'config.json'
        tmp = directory / 'config.next.json'
        tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp, path)
    return len(config['proxies'])


def check_ports():
    for port in (PORT, CONTROLLER):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))


def run():
    CLIENT.mkdir(parents=True, exist_ok=True)
    check_ports()  # Never replace another process, even one using our intended port.
    STOP.unlink(missing_ok=True)
    REFRESH.unlink(missing_ok=True)
    deployment = json.loads((ROOT / '.private/deployment.json').read_text(encoding='utf-8'))
    executable = Path(deployment['client_executable'])
    if not executable.is_file():
        raise FileNotFoundError('Configured Mihomo executable is unavailable')
    http = direct_session()
    count = fetch_config(http, deployment)
    core_home = Path(deployment.get('client_runtime', str(CLIENT)))
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with (CLIENT / 'core.log').open('a', encoding='utf-8') as log:
        child = subprocess.Popen([str(executable), '-d', str(core_home), '-f', str(core_home / 'config.json')],
                                 stdout=log, stderr=subprocess.STDOUT, creationflags=creation_flags)
        state = {'status': 'starting', 'supervisor_pid': os.getpid(), 'client_pid': child.pid,
                 'proxy': f'http://127.0.0.1:{PORT}', 'nodes': count,
                 'last_refresh': datetime.now(timezone.utc).isoformat()}
        headers = {'Authorization': 'Bearer ' + deployment['controller_secret']}
        try:
            for attempt in range(20):
                if child.poll() is not None:
                    raise RuntimeError('Independent client exited during startup')
                try:
                    response = http.get(f'http://127.0.0.1:{CONTROLLER}/version', headers=headers, timeout=1)
                    response.raise_for_status()
                    break
                except requests.RequestException:
                    time.sleep(0.5)
            else:
                raise RuntimeError('Independent controller did not become ready')
            state['status'] = 'running'
            write_state(**state)
            next_refresh = time.monotonic() + 1800
            while not STOP.exists():
                if child.poll() is not None:
                    raise RuntimeError('Independent client stopped unexpectedly; check core.log')
                if REFRESH.exists() or time.monotonic() >= next_refresh:
                    REFRESH.unlink(missing_ok=True)
                    try:
                        count = fetch_config(http, deployment)
                        response = http.put(f'http://127.0.0.1:{CONTROLLER}/configs?force=true',
                                            headers=headers, json={'path': str(core_home / 'config.json')}, timeout=10)
                        response.raise_for_status()
                        state.update(status='running', nodes=count, last_refresh=datetime.now(timezone.utc).isoformat())
                        state.pop('refresh_error', None)
                    except Exception as error:
                        state.update(status='degraded', refresh_error=type(error).__name__)
                    write_state(**state)
                    next_refresh = time.monotonic() + 1800
                time.sleep(1)
        except Exception as error:
            state.update(status='error', error=str(error))
            write_state(**state)
            raise
        finally:
            # Terminate the exact child HANDLE we created, not a process name or PID search.
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
            if STOP.exists():
                STOP.unlink(missing_ok=True)
                state['status'] = 'stopped'
                write_state(**state)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'run', 'stop', 'status', 'refresh'])
    args = parser.parse_args()
    CLIENT.mkdir(parents=True, exist_ok=True)
    if args.action == 'run':
        run()
    elif args.action == 'stop':
        STOP.write_text('stop only this project supervisor\n', encoding='utf-8')
        print('Stop requested for the independent client; existing system proxy is untouched.')
    elif args.action == 'status':
        print(STATE.read_text(encoding='utf-8') if STATE.exists() else 'Not started')
    elif args.action == 'refresh':
        REFRESH.write_text('refresh this project subscription\n', encoding='utf-8')
        print('Subscription refresh requested. Check status for the updated timestamp.')
    else:
        deployment = json.loads((ROOT / '.private/deployment.json').read_text(encoding='utf-8'))
        try:
            with direct_session() as http:
                response = http.get(f'http://127.0.0.1:{CONTROLLER}/version',
                                    headers={'Authorization': 'Bearer ' + deployment['controller_secret']}, timeout=1)
                if response.ok:
                    print(f'Independent client is already running: http://127.0.0.1:{PORT}')
                    return
        except requests.RequestException:
            pass
        check_ports()
        with (CLIENT / 'supervisor.log').open('a', encoding='utf-8') as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'run'], cwd=ROOT,
                                     stdout=log, stderr=subprocess.STDOUT,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        print(f'Independent supervisor started: {child.pid}. Proxy: http://127.0.0.1:{PORT}')


if __name__ == '__main__':
    main()
