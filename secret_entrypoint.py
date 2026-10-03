"""Load only a service's mounted secrets; never print values or accept arbitrary env keys."""
import json
import os
from pathlib import Path
import sys

ALLOWED = {'DATABASE_URL', 'READONLY_DATABASE_URL', 'RF_IDENTITY_SERVICE_KEY',
           'RF_WORKSPACES', 'RF_BOOTSTRAP_PASSWORD', 'RF_MIGRATOR_PASSWORD',
           'RF_APP_PASSWORD', 'RF_READONLY_PASSWORD'}


def load(path='/run/secrets/runtime.json'):
    values = json.loads(Path(path).read_text())
    if not isinstance(values, dict) or set(values) - ALLOWED or any(not isinstance(v, str) for v in values.values()):
        raise ValueError('Invalid secret keys or types')
    os.environ.update(values)


def main():
    try:
        load()
        if len(sys.argv) < 2:
            raise ValueError('Missing command')
    except Exception:
        print('Secret configuration rejected', file=sys.stderr)
        return 1
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == '__main__':
    raise SystemExit(main())
