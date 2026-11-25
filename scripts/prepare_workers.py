"""Vendor pinned upstream modules and apply small, verified patches."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT.parent / 'network-gate-workbench'
OUT = ROOT / 'workers' / 'vendor'
OUT.mkdir(parents=True, exist_ok=True)
manifest = {}
for name, folder in [('checker', 'checker-upstream'), ('edgetunnel', 'edgetunnel-upstream')]:
    source = UPSTREAM / folder
    text = (source / '_worker.js').read_text(encoding='utf-8-sig')
    commit = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    manifest[name] = {'commit': commit, 'upstream_sha256': hashlib.sha256(text.encode()).hexdigest()}
    if name == 'checker':
        patches = {
            'async function checkProxy({ type, value }, colo) {': 'export async function checkProxy({ type, value }, colo, lookupSource = "iplocate") {',
            "const targetHost = 'www.iplocate.io';": "const targetHost = lookupSource === 'cloudflare' ? 'www.cloudflare.com' : 'www.iplocate.io';",
            "'GET /api/lookup HTTP/1.1',": "`GET ${lookupSource === 'cloudflare' ? '/cdn-cgi/trace' : '/api/lookup'} HTTP/1.1`,",
            'exit = JSON.parse(bodyText.trim());': """if (lookupSource === 'cloudflare') {
                    const trace = Object.fromEntries(bodyText.trim().split('\\n').map(line => { const i=line.indexOf('='); return [line.slice(0,i),line.slice(i+1)]; }));
                    if (!trace.ip) throw new Error('Missing trace exit IP');
                    exit = { ip: trace.ip, country_code: trace.loc, lookup_source: 'cloudflare', privacy: {} };
                } else {
                    exit = JSON.parse(bodyText.trim());
                    if (!exit || typeof exit.ip !== 'string') throw new Error('Missing lookup exit IP');
                }""",
        }
        for old, new in patches.items():
            if text.count(old) != 1:
                raise RuntimeError(f'Upstream changed: {old}')
            text = text.replace(old, new)
        for key in ['is_hosting', 'is_bogon', 'is_proxy', 'is_vpn', 'is_tor', 'is_abuser']:
            old = f'exit.privacy?.{key} || false'
            if text.count(old) != 1:
                raise RuntimeError(f'Upstream changed: {key}')
            text = text.replace(old, f"typeof exit.privacy?.{key} === 'boolean' ? exit.privacy.{key} : null")
    (OUT / f'{name}.mjs').write_text(text, encoding='utf-8')
    license_path = source / 'LICENSE'
    if license_path.exists():
        (OUT / f'{name}.LICENSE').write_bytes(license_path.read_bytes())
    manifest[name]['patched_sha256'] = hashlib.sha256(text.encode()).hexdigest()
(ROOT / 'workers' / 'upstream.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('Prepared pinned Worker modules:', ', '.join(manifest))
