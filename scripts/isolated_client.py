"""Start/stop only the independently managed Network Gate client."""
import argparse
import contextlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.deps')]
from scripts.client_runtime import (  # noqa: E402
    CLIENT,
    CREATE_FLAGS,
    DISABLED,
    REFRESH,
    STATE,
    STOP,
    acquire_lock,
    supervise,
)
from scripts.client_runtime import isolate_config as isolate_config  # noqa: E402,F401


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'run', 'stop', 'status', 'refresh'])
    parser.add_argument('--scheduled', action='store_true')
    args = parser.parse_args()
    CLIENT.mkdir(parents=True, exist_ok=True)
    if args.action == 'run':
        if args.scheduled:
            with (CLIENT / 'supervisor.log').open('a', encoding='utf-8') as log:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    supervise(scheduled=True)
        else:
            supervise()
    elif args.action == 'stop':
        DISABLED.write_text('User stopped independent Network Gate\n', encoding='utf-8')
        STOP.write_text('stop only this project\n', encoding='utf-8')
        print('Independent client stop requested; automatic restart remains disabled until Start.')
    elif args.action == 'refresh':
        REFRESH.write_text('refresh\n', encoding='utf-8')
        print('Subscription refresh requested')
    elif args.action == 'status':
        state = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else {'status': 'not_started'}
        state['manually_disabled'] = DISABLED.exists()
        lock = acquire_lock()
        if lock is not None:
            lock.close()
            state['status'] = 'stopped' if DISABLED.exists() else 'awaiting_watchdog_or_start'
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        lock = acquire_lock()
        if lock is None:
            print('Independent supervisor is already running')
            return
        lock.close()
        DISABLED.unlink(missing_ok=True)
        STOP.unlink(missing_ok=True)
        with (CLIENT / 'supervisor.log').open('a', encoding='utf-8') as log:
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'run'], cwd=ROOT,
                                     stdout=log, stderr=subprocess.STDOUT, creationflags=CREATE_FLAGS)
        print(f'Independent supervisor started: {child.pid}. Proxy: http://127.0.0.1:17898')


if __name__ == '__main__':
    main()
