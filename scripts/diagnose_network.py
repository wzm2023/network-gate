"""Measure real HTTPS traffic on a separate loopback-only diagnostic client."""
import argparse
import concurrent.futures
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
import requests  # noqa: E402

from scripts.child_job import ChildJob  # noqa: E402
from scripts.client_runtime import CREATE_FLAGS, atomic_json, direct_session  # noqa: E402

PORT, CONTROL = 17908, 19093


def request_sample(label, url, proxy, expected_bytes=None):
    started = time.monotonic()
    result = {'test': label, 'url': url, 'bytes': 0, 'complete': False}
    sample = bytearray()
    try:
        with direct_session() as http:
            with http.get(url, proxies={'https': proxy, 'http': proxy},
                          headers={'Connection': 'close', 'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36'},
                          timeout=(15, 15), stream=True) as response:
                result.update(http=response.status_code, headers_s=round(time.monotonic() - started, 3))
                for chunk in response.iter_content(16384):
                    result['bytes'] += len(chunk)
                    if len(sample) < 4096:
                        sample.extend(chunk[:4096-len(sample)])
                    if time.monotonic() - started > 60:
                        raise TimeoutError('Transfer exceeded 60 seconds')
                    if expected_bytes is None and result['bytes'] >= 131072:
                        result['sample_limited'] = True
                        break
                result['complete'] = expected_bytes is None or result['bytes'] == expected_bytes
                result['ok'] = result['complete'] and 200 <= response.status_code < 300
                if label == 'trace':
                    trace = dict(x.split('=', 1) for x in sample.decode(errors='replace').splitlines() if '=' in x)
                    result.update(exit_ip=trace.get('ip'), country=trace.get('loc'))
                if response.status_code >= 400:
                    result['response_hint'] = sample.decode(errors='replace')[:250]
    except Exception as error:
        result.update(ok=False, error=type(error).__name__, detail=str(error)[:200])
    result['seconds'] = round(time.monotonic() - started, 3)
    if expected_bytes:
        result['expected_bytes'] = expected_bytes
        result['mbps_including_setup'] = round(result['bytes'] * 8 / max(result['seconds'], .001) / 1e6, 3)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--names', nargs='+', required=True)
    parser.add_argument('--rounds', type=int, default=1)
    parser.add_argument('--bytes', type=int, default=524288)
    parser.add_argument('--report', default='network-diagnosis.json')
    parser.add_argument('--baseline-port', type=int)
    parser.add_argument('--snapshot', type=Path, default=ROOT / '.private/client/subscription.json')
    parser.add_argument('--tests', nargs='+', choices=['trace', 'google204', 'youtube204', 'tiktok', 'download'])
    args = parser.parse_args()
    deployment = json.loads((ROOT / '.private/deployment.json').read_text(encoding='utf-8'))
    source = json.loads(args.snapshot.read_text(encoding='utf-8'))
    proxies = [p for p in source['proxies'] if p['name'] in args.names]
    if len(proxies) != len(set(args.names)):
        raise ValueError('A requested node is absent from the cached snapshot')
    tests = [('trace', 'https://www.cloudflare.com/cdn-cgi/trace', None),
             ('google204', 'https://www.google.com/generate_204', None),
             ('youtube204', 'https://www.youtube.com/generate_204', None),
             ('tiktok', 'https://www.tiktok.com/', None),
             ('download', f'https://speed.cloudflare.com/__down?bytes={args.bytes}', args.bytes)]
    if args.tests:
        tests = [item for item in tests if item[0] in args.tests]
    report = {'started_at': datetime.now(timezone.utc).isoformat(),
              'snapshot': source['x-gate']['generated_at'], 'nodes': {}, 'baseline': []}
    atomic_json(ROOT / '.private/diagnostic-snapshot.json', source)
    path = ROOT / '.private' / Path(args.report).name
    runtime = Path(os.environ['LOCALAPPDATA']) / 'NetworkGate-20261126/diagnostics'
    runtime.mkdir(parents=True, exist_ok=True)
    for port in (PORT, CONTROL):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    config = {'mixed-port': PORT, 'bind-address': '127.0.0.1', 'allow-lan': False,
              'external-controller': f'127.0.0.1:{CONTROL}', 'secret': deployment['controller_secret'],
              'mode': 'rule', 'log-level': 'warning', 'ipv6': False, 'tun': {'enable': False},
              'dns': {'enable': False}, 'profile': {'store-selected': False, 'store-fake-ip': False},
              'proxies': proxies, 'proxy-groups': [{'name': 'DIAG', 'type': 'select', 'proxies': [p['name'] for p in proxies]}],
              'rules': ['MATCH,DIAG']}
    atomic_json(runtime / 'config.json', config)
    job, child = ChildJob(), None
    try:
        with (runtime / 'core.log').open('a', encoding='utf-8') as log:
            child = subprocess.Popen([deployment['client_executable'], '-d', str(runtime), '-f', str(runtime/'config.json')],
                                     stdout=log, stderr=subprocess.STDOUT, creationflags=CREATE_FLAGS)
            job.add(child)
            with direct_session() as http:
                http.headers['Authorization'] = 'Bearer ' + deployment['controller_secret']
                for _ in range(30):
                    try:
                        http.get(f'http://127.0.0.1:{CONTROL}/version', timeout=1).raise_for_status()
                        break
                    except requests.RequestException:
                        time.sleep(.5)
                else:
                    raise RuntimeError('Diagnostic client did not start')
                if args.baseline_port:
                    for label, url, size in tests:
                        result = request_sample(label, url, f'http://127.0.0.1:{args.baseline_port}', size)
                        report['baseline'].append(result)
                        print(json.dumps({'node': 'existing-proxy-baseline', **result}, ensure_ascii=False), flush=True)
                        atomic_json(path, report)
                for name in args.names:
                    http.put(f'http://127.0.0.1:{CONTROL}/proxies/DIAG', json={'name': name}, timeout=5).raise_for_status()
                    report['nodes'][name] = {'metadata': source['x-gate']['nodes'][name], 'samples': []}
                    for round_number in range(1, args.rounds + 1):
                        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                            pending = [pool.submit(request_sample, label, url, f'http://127.0.0.1:{PORT}', size) for label, url, size in tests]
                            for future in concurrent.futures.as_completed(pending):
                                result = {**future.result(), 'round': round_number}
                                report['nodes'][name]['samples'].append(result)
                                print(json.dumps({'node': name, **result}, ensure_ascii=False), flush=True)
                                atomic_json(path, report)
                        if round_number < args.rounds:
                            time.sleep(5)
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=10)
        job.close()
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    atomic_json(path, report)
    print('Report:', path, flush=True)


if __name__ == '__main__':
    main()
