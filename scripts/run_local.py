"""Run node discovery through the existing proxy, without changing OS settings."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-nodes', type=int, default=12)
    parser.add_argument('--concurrency', type=int, default=3)
    parser.add_argument('--output', default='runtime/smoke')
    parser.add_argument('--proxy', default='http://127.0.0.1:7897')
    args = parser.parse_args()
    state = json.loads((ROOT / '.private/deployment.json').read_text(encoding='utf-8'))
    os.environ.update({
        'CHECK_WORKER': 'https://' + state['checker_host'] + '/check?sstp=vpn:vpn@',
        'CHECK_TOKEN': state['check_token'], 'EDGE_HOSTS': state['edge_host'] + ':443',
        'MAX_CHECK_NODES': str(args.max_nodes), 'CHECK_CONCURRENCY': str(args.concurrency),
        'CHECK_TIMEOUT': '40', 'CHECK_DEADLINE': '300', 'MAX_LATENCY_MS': '30000',
        'HTTP_TIMEOUT': '30', 'PUBLIC_DIR': str(ROOT / args.output),
        'HTTP_PROXY': args.proxy, 'HTTPS_PROXY': args.proxy, 'NO_PROXY': 'localhost,127.0.0.1',
        'GITHUB_REPOSITORY': 'wzm2023/network-gate',
    })
    import vpngate
    vpngate.main()


if __name__ == '__main__':
    main()
