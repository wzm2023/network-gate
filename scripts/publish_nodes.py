"""Publish only validated node data, with a separate publishing credential."""
import json
import os
from pathlib import Path

import requests

host = os.environ['EDGE_HOST']
token = os.environ['PUBLISH_TOKEN']
path = Path(os.environ.get('PUBLIC_DIR', 'public')) / 'data.json'
data = json.loads(path.read_text(encoding='utf-8'))
response = requests.post('https://' + host + '/publish', json=data,
                         headers={'Authorization': 'Bearer ' + token}, timeout=30)
if response.status_code == 409 and response.json().get('error') == 'older_snapshot_rejected':
    print('Newer snapshot is already published; keeping it')
    raise SystemExit(0)
response.raise_for_status()
result = response.json()
if result.get('ok') is not True:
    raise RuntimeError('Worker rejected node snapshot')
print(f"Worker snapshot updated: {result['count']} nodes")
