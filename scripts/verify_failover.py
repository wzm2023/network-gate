"""Temporarily fail one isolated client node, then verify and restore its config."""
import copy
import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
from scripts.client_runtime import CLIENT, control, direct_session  # noqa: E402


def main():
    deployment = json.loads((ROOT / '.private/deployment.json').read_text())
    runtime = Path(deployment['client_runtime'])
    http = direct_session()
    for _ in range(120):
        state = json.loads((CLIENT / 'supervisor.json').read_text())
        if state.get('status') == 'running' and state.get('selected') not in [None, 'REJECT']:
            break
        time.sleep(1)
    else:
        raise RuntimeError('No working residential candidate before fault test')
    target = state['selected']
    baseline = state.get('switches', 0)
    config = json.loads((runtime / 'config.json').read_text())
    if len(config['proxies']) < 2:
        raise RuntimeError('At least two residential candidates are required for live failover testing')
    broken = copy.deepcopy(config)
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        unused_port = reserved.getsockname()[1]
        # Keep the TCP port reserved but not listening during the test.
        for node in broken['proxies']:
            if node['name'] == target:
                node.update(server='127.0.0.1', port=unused_port)
        fault = runtime / 'fault-test.json'
        fault.write_text(json.dumps(broken), encoding='utf-8')
        report = {'failed_node': target, 'passed': False}
        started = time.monotonic()
        try:
            control(http, deployment, 'PUT', '/configs?force=true', json={'path': str(fault)})
            control(http, deployment, 'PUT', '/proxies/GATE', json={'name': target})
            print('Independent node fault injected:', target, flush=True)
            for _ in range(180):
                state = json.loads((CLIENT / 'supervisor.json').read_text())
                if state.get('status') == 'running' and state.get('selected') not in [target, 'REJECT', None] and state.get('switches', 0) > baseline:
                    report.update(passed=True, recovery_seconds=round(time.monotonic() - started, 1),
                                  recovered_node=state['selected'], exit_ip=state.get('exit_ip'))
                    print(json.dumps(report, ensure_ascii=False), flush=True)
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Automatic failover did not recover within 180 seconds')
        finally:
            control(http, deployment, 'PUT', '/configs?force=true', json={'path': str(runtime / 'config.json')})
            selected = state.get('selected')
            if selected and selected != 'REJECT':
                control(http, deployment, 'PUT', '/proxies/GATE', json={'name': selected})
            (ROOT / '.private/failover-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            fault.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
