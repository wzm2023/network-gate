"""Refresh this project's KV through its authenticated entry, independent of GitHub cron."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
import requests  # noqa: E402

import vpngate  # noqa: E402


def main():
    state = json.loads((ROOT / '.private/deployment.json').read_text(encoding='utf-8'))
    entry = 'https://' + state['edge_host']
    http = requests.Session()
    http.trust_env = False
    response = http.get(entry + '/discovery', headers={'Authorization': 'Bearer ' + state['check_token']}, timeout=25)
    response.raise_for_status()
    rows = vpngate.parse_csv(response.text)
    if not rows:
        raise RuntimeError('Empty discovery response')
    output = ROOT / '.private/local-scan'
    # Only this child process gets these settings. No global proxy or DNS changes.
    for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
        os.environ.pop(key, None)
    os.environ.update(CHECK_WORKER=entry + '/check?sstp=vpn:vpn@', CHECK_TOKEN=state['check_token'],
                      EDGE_HOSTS=state['edge_host'] + ':443', CHECK_CONCURRENCY='4', CHECK_TIMEOUT='35',
                      CHECK_DEADLINE='600', HTTP_TIMEOUT='25', MAX_CHECK_NODES='100',
                      MIN_SUCCESS_COUNT='3', MIN_SUCCESS_RATIO='0.03', RESULT_TTL_SECONDS='7200',
                      PUBLIC_DIR=str(output), GITHUB_REPOSITORY='wzm2023/network-gate')
    vpngate.main(rows_override=rows, source_override='authenticated-entry-discovery')
    data = json.loads((output / 'data.json').read_text(encoding='utf-8'))
    response = http.post(entry + '/publish', json=data,
                         headers={'Authorization': 'Bearer ' + state['publish_token']}, timeout=25)
    # A cloud run may have published a newer snapshot in the meantime.
    if response.status_code == 409:
        print('Newer cloud snapshot already published')
        return
    response.raise_for_status()
    print('Local fallback refresh published:', data['stats']['success'], 'nodes;',
          data['stats']['residential_est'], 'residential candidates')


if __name__ == '__main__':
    main()
