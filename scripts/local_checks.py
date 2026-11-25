"""Run isolated Python dependencies and lint via stdin on restrictive Windows hosts."""
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
os.chdir(root)
env = {**os.environ, 'PYTHONPATH': str(root / '.deps')}
subprocess.run([sys.executable, '-m', 'pytest'], env=env, check=True)
files = [root / 'vpngate.py', *(root / 'tests').glob('*.py'), *(root / 'scripts').glob('*.py')]
for path in files:
    subprocess.run([sys.executable, '-m', 'ruff', 'check', '--isolated', '--no-cache',
                    '--select', 'E,F,W,I', '--ignore', 'E501', '--config', 'lint.isort.known-first-party=["vpngate"]', '--stdin-filename', path.name, '-'],
                   input=path.read_text(encoding='utf-8'), text=True, env=env, check=True)
subprocess.run(['node', '--experimental-loader', './tests/cloudflare-loader.mjs', '--test', *map(str, (root / 'tests').glob('*.test.mjs'))], check=True)
print('All local checks passed')
