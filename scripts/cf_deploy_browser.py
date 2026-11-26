"""Deploy only this project's Workers via the user's authenticated dashboard tab.

No browser cookies or Cloudflare account tokens are exported. Runtime secrets stay
in .private and are sent only to the user's Cloudflare account as secret bindings.
"""
import argparse
import json
import secrets
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / '.private'
STATE = PRIVATE / 'deployment.json'


def evaluate(target, code):
    req = urllib.request.Request(f'http://127.0.0.1:3456/eval?target={target}',
                                 data=code.encode(), headers={'Content-Type': 'text/plain'})
    with urllib.request.urlopen(req, timeout=55) as response:
        result = json.load(response)
    if result.get('error'):
        raise RuntimeError(result['error'])
    value = result.get('value')
    return json.loads(value) if isinstance(value, str) else value


def api(target, account, path, method='GET', body=None):
    code = '''(async()=>{
      const r=await fetch(PATH,{method:METHOD,headers:{'Content-Type':'application/json'},
        signal:AbortSignal.timeout(20000),body:BODY});
      const text=await r.text();let d;
      try{d=JSON.parse(text);}catch{d={success:false,errors:['non_json_response']};}
      return JSON.stringify({status:r.status,...d});
    })()'''.replace('PATH', json.dumps('/api/v4/accounts/' + account + path)).replace(
        'METHOD', json.dumps(method)).replace('BODY', 'undefined' if body is None else json.dumps(json.dumps(body)))
    result = evaluate(target, code)
    if not result.get('success'):
        raise RuntimeError(json.dumps({'status': result.get('status'), 'errors': result.get('errors')}))
    return result['result']


def upload(target, state, kind):
    if kind == 'checker':
        files = ['checker.mjs', 'policy.mjs', 'vendor/checker.mjs']
        bindings = [{'name': 'CHECK_TOKEN', 'type': 'secret_text', 'text': state['check_token']}]
    else:
        files = ['edge.mjs', 'policy.mjs', 'sstp-tcp.mjs', 'vendor/edgetunnel.mjs']
        bindings = [{'name': key, 'type': 'secret_text', 'text': state[value]} for key, value in
                    [('ADMIN', 'admin_password'), ('KEY', 'admin_key'), ('UUID', 'uuid'), ('SUB_TOKEN', 'sub_token'), ('PUBLISH_TOKEN', 'publish_token'), ('CHECK_TOKEN', 'check_token')]]
        bindings += [{'name': 'KV', 'type': 'kv_namespace', 'namespace_id': state['kv_namespace_id']},
                     {'name': 'HOST', 'type': 'plain_text', 'text': state['edge_host']},
                     {'name': 'NODE_LIST_URL', 'type': 'plain_text', 'text': state['node_list_url']},
                     {'name': 'CHECKER', 'type': 'service', 'service': state['checker_name']},
                     {'name': 'RESIDENTIAL_ONLY', 'type': 'plain_text', 'text': str(state.get('residential_only', False)).lower()},
                     {'name': 'TCP_CONCURRENT_DIAL', 'type': 'plain_text', 'text': '1'},
                     {'name': 'PROXY_CONCURRENT_DIAL', 'type': 'plain_text', 'text': '1'}]
    metadata = {'main_module': kind + '.mjs', 'compatibility_date': '2025-11-04', 'bindings': bindings,
                'observability': {'enabled': True, 'head_sampling_rate': state.get('observability_sampling', 0.1)},
                'tags': ['network-gate-20261126']}
    sources = {name: (ROOT / 'workers' / name).read_text(encoding='utf-8') for name in files}
    script = state[kind + '_name']
    url = '/api/v4/accounts/' + state['account_id'] + '/workers/scripts/' + script
    code = '''(async()=>{
      const form=new FormData();
      form.append('metadata',new Blob([JSON.stringify(METADATA)],{type:'application/json'}));
      for(const [name,source] of Object.entries(SOURCES))form.append(name,new Blob([source],{type:'application/javascript+module'}),name);
      const r=await fetch(URL,{method:'PUT',body:form});const d=await r.json();
      return JSON.stringify({status:r.status,success:d.success,errors:d.errors,result:d.result?{id:d.result.id,deployment_id:d.result.deployment_id,startup_time_ms:d.result.startup_time_ms}:null});
    })()'''.replace('URL', json.dumps(url)).replace('METADATA', json.dumps(metadata)).replace('SOURCES', json.dumps(sources))
    result = evaluate(target, code)
    if not result.get('success'):
        raise RuntimeError(json.dumps(result))
    api(target, state['account_id'], '/workers/scripts/' + script + '/subdomain', 'POST', {'enabled': True})
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', required=True)
    parser.add_argument('--account', required=True)
    parser.add_argument('--kind', choices=['checker', 'edge', 'both'], default='both')
    args = parser.parse_args()
    PRIVATE.mkdir(exist_ok=True)
    if STATE.exists():
        state = json.loads(STATE.read_text(encoding='utf-8'))
        if state['account_id'] != args.account:
            raise RuntimeError('Account mismatch')
    else:
        domain = api(args.target, args.account, '/workers/subdomain')['subdomain']
        existing = {x['id'] for x in api(args.target, args.account, '/workers/scripts')}
        if existing & {'network-gate-checker', 'network-gate-edge'}:
            raise RuntimeError('Refusing to overwrite existing names without project deployment state')
        state = {'account_id': args.account, 'subdomain': domain,
                 'checker_name': 'network-gate-checker', 'edge_name': 'network-gate-edge',
                 'edge_host': f'network-gate-edge.{domain}.workers.dev',
                 'checker_host': f'network-gate-checker.{domain}.workers.dev',
                 'node_list_url': 'https://wzm2023.github.io/network-gate/data.json',
                 'check_token': secrets.token_urlsafe(36), 'sub_token': secrets.token_urlsafe(36),
                 'admin_password': secrets.token_urlsafe(30), 'admin_key': secrets.token_urlsafe(30),
                 'uuid': str(uuid.uuid4())}
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
    if 'publish_token' not in state:
        state['publish_token'] = secrets.token_urlsafe(36)
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
    if args.kind in ('edge', 'both') and 'kv_namespace_id' not in state:
        namespace = api(args.target, args.account, '/storage/kv/namespaces', 'POST', {'title': 'network-gate-config'})
        state['kv_namespace_id'] = namespace['id']
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
    for kind in (['checker', 'edge'] if args.kind == 'both' else [args.kind]):
        result = upload(args.target, state, kind)
        state[kind + '_deployment'] = result['result']
        STATE.write_text(json.dumps(state, indent=2), encoding='utf-8')
        print(json.dumps({'worker': state[kind + '_name'], **result}, ensure_ascii=False))


if __name__ == '__main__':
    main()
