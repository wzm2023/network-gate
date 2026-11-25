"""Small explicit-proxy checks; never changes Windows or existing Clash settings."""
import argparse
import json
import time
from pathlib import Path

import requests

parser = argparse.ArgumentParser()
parser.add_argument('--bytes', type=int, default=5242880)
parser.add_argument('--countries', type=int, default=3)
parser.add_argument('--names', nargs='+', help='Exact node names to verify instead of country sampling')
parser.add_argument('--report', default='client-verification.json')
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
state = json.loads((root / '.private/deployment.json').read_text(encoding='utf-8'))
config = json.loads((root / '.private/client/config.json').read_text(encoding='utf-8'))
http = requests.Session()
http.trust_env = False
headers = {'Authorization': 'Bearer ' + state['controller_secret']}
controller = 'http://127.0.0.1:19091'
proxy = {'http': 'http://127.0.0.1:17898', 'https': 'http://127.0.0.1:17898'}
choices = []
countries = set()
for node in config['proxies']:
    country = node['name'].split('-')[0]
    if country not in countries:
        choices.append(node['name'])
        countries.add(country)
    if len(choices) >= args.countries:
        break
report = []
if args.names:
    available = {node['name'] for node in config['proxies']}
    if any(name not in available for name in args.names):
        raise ValueError('Requested node is absent from the current subscription')
    choices = args.names
try:
    for name in choices:
        response = http.put(controller + '/proxies/GATE', headers=headers, json={'name': name}, timeout=5)
        response.raise_for_status()
        sample = {'node': name}
        started = time.monotonic()
        try:
            # Each selection needs fresh TCP connections; pooled HTTP keepalive
            # would keep using the previous node even after changing GATE.
            transfer = requests.Session()
            transfer.trust_env = False
            transfer.headers['Connection'] = 'close'
            response = transfer.get('https://www.cloudflare.com/cdn-cgi/trace', proxies=proxy, timeout=20)
            response.raise_for_status()
            trace = dict(line.split('=', 1) for line in response.text.splitlines() if '=' in line)
            sample.update(exit_ip=trace.get('ip'), country=trace.get('loc'), connect_s=round(time.monotonic()-started, 3))
            started = time.monotonic()
            size = 0
            with transfer.get(f'https://speed.cloudflare.com/__down?bytes={args.bytes}', proxies=proxy, timeout=25, stream=True) as download:
                download.raise_for_status()
                for chunk in download.iter_content(65536):
                    size += len(chunk)
                    if time.monotonic() - started > 90:
                        raise TimeoutError('Download exceeded 90 seconds')
            elapsed = time.monotonic() - started
            sample.update(download_bytes=size, download_s=round(elapsed, 3), mbps=round(size * 8 / elapsed / 1e6, 3), ok=size == args.bytes)
        except Exception as error:
            sample.update(ok=False, elapsed_s=round(time.monotonic() - started, 3), error=type(error).__name__ + ': ' + str(error)[:180])
        finally:
            transfer.close()
        report.append(sample)
        print(json.dumps(sample, ensure_ascii=False), flush=True)
finally:
    http.put(controller + '/proxies/GATE', headers=headers, json={'name': 'GATE-FALLBACK'}, timeout=5).raise_for_status()
(root / '.private' / Path(args.report).name).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
