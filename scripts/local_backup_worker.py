"""Container-side backup helpers. Only private stdin/stdout carries database content."""
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def identity_snapshot(c):
    if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or c.execute('PRAGMA user_version').fetchone()[0] != 1:
        raise ValueError('Invalid identity database')
    result = {}
    for table in ('accounts', 'audit', 'sessions', 'sqlite_sequence'):
        rows = sorted(c.execute('SELECT * FROM '+table).fetchall(), key=repr)
        result[table] = {'count': len(rows), 'sha256': hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
    return result


def main():
    action = sys.argv[1]
    if action == 'identity-export':
        with sqlite3.connect('file:/data/identity.sqlite3?mode=ro', uri=True) as source:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)/'identity.sqlite3'
                with sqlite3.connect(path) as target:
                    source.backup(target)
                    summary = identity_snapshot(target)
                print(json.dumps({'data': base64.b64encode(path.read_bytes()).decode(), 'snapshot': summary}))
    elif action == 'identity-import':
        request = json.load(sys.stdin)
        path = Path('/data/identity.sqlite3')
        with path.open('xb') as f:
            f.write(base64.b64decode(request['data'], validate=True))
        os.chmod(path, 0o600)
        with sqlite3.connect(path) as c:
            if identity_snapshot(c) != request['snapshot']:
                raise ValueError('Identity fingerprint mismatch')
            c.execute('DELETE FROM sessions')
        print(json.dumps({'accounts_and_audit_equal': True, 'old_sessions_revoked': True}))
    elif action == 'business-snapshot':
        from scripts.backup_snapshot import snapshot_session
        with snapshot_session(os.environ['DATABASE_URL']) as result:
            print(json.dumps(result))
    elif action == 'business-verify':
        from scripts.restore_validation import validate_restore
        print(json.dumps(validate_restore(os.environ['DATABASE_URL'], json.load(sys.stdin))))
    else:
        raise ValueError('Unknown action')


if __name__ == '__main__':
    try: main()
    except Exception as error:
        print(json.dumps({'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
