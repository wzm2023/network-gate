"""Persist a small successful-maintenance record; never changes workflow code."""
import base64
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

repo = os.environ['GITHUB_REPOSITORY']
url = f'https://api.github.com/repos/{repo}/contents/.github/maintenance.json'
headers = {'Authorization': 'Bearer ' + os.environ['GH_TOKEN'], 'Accept': 'application/vnd.github+json',
           'User-Agent': 'network-gate-maintenance'}
now = datetime.now(timezone.utc)
sha = None
try:
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as response:
        current = json.load(response)
    sha = current['sha']
    last = json.loads(base64.b64decode(current['content']))
    if (now - datetime.fromisoformat(last['last_success'])).days < 14:
        print('Maintenance record is recent; no commit needed')
        raise SystemExit(0)
except urllib.error.HTTPError as error:
    if error.code != 404:
        raise
content = json.dumps({'last_success': now.isoformat(), 'run_id': os.environ.get('GITHUB_RUN_ID')}, indent=2) + '\n'
body = {'message': 'chore: record successful node-list maintenance',
        'content': base64.b64encode(content.encode()).decode(), 'branch': 'main'}
if sha:
    body['sha'] = sha
headers['Content-Type'] = 'application/json'
with urllib.request.urlopen(urllib.request.Request(url, headers=headers, data=json.dumps(body).encode(), method='PUT'), timeout=20) as response:
    json.load(response)
print('Maintenance record updated')
