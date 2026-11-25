"""Configure this project's GitHub Secrets and Pages, using the existing gh login."""
import json
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
state = json.loads((root / '.private/deployment.json').read_text(encoding='utf-8'))
repo = 'wzm2023/network-gate'
env = {**os.environ, 'HTTPS_PROXY': 'http://127.0.0.1:7897', 'HTTP_PROXY': 'http://127.0.0.1:7897'}


def gh(*args, stdin=None):
    run = subprocess.run(['gh', *args], input=stdin, text=True, capture_output=True, env=env)
    if run.returncode:
        raise RuntimeError(run.stderr.strip() or run.stdout[:300])
    return run.stdout


gh('secret', 'set', 'CHECK_TOKEN', '--repo', repo, stdin=state['check_token'])
gh('secret', 'set', 'PUBLISH_TOKEN', '--repo', repo, stdin=state['publish_token'])
for name, value in [('CHECKER_HOST', state['checker_host']), ('EDGE_HOST', state['edge_host'])]:
    gh('variable', 'set', name, '--repo', repo, '--body', value)
print('Checker secret and endpoint variables configured')
try:
    gh('api', 'repos/' + repo + '/pages', '--method', 'POST', '-f', 'build_type=workflow')
except RuntimeError:
    status = json.loads(gh('api', 'repos/' + repo + '/pages'))
    if status.get('build_type') != 'workflow':
        gh('api', 'repos/' + repo + '/pages', '--method', 'PUT', '-f', 'build_type=workflow')
print('GitHub Pages configured for Actions')
