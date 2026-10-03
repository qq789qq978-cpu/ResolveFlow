"""Small, single-host identity store. Explicit initialization; no business data.

SQLite is confined to the gateway's private persistent volume. BEGIN IMMEDIATE
serializes account-cap and last-manager checks across gateway processes.
"""
from contextlib import contextmanager
import argparse
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

ROLES = {'operator', 'reviewer', 'admin'}
SESSION_SECONDS = 8 * 60 * 60
PUBLIC = 'id,username,role,workspace,enabled,created_at'
SCHEMA = '''
CREATE TABLE accounts (
 id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('operator','reviewer','admin','manager')),
 workspace TEXT,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
 failures INTEGER NOT NULL DEFAULT 0, locked_until INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL,
 CHECK ((role='manager' AND workspace IS NULL) OR
        (role!='manager' AND workspace IS NOT NULL))
);
CREATE TABLE sessions (
 digest TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX sessions_account ON sessions(account_id);
CREATE TABLE audit (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL,
 actor TEXT, workspace TEXT, action TEXT NOT NULL, target TEXT, status INTEGER NOT NULL
);
CREATE INDEX audit_scope ON audit(workspace,id);
PRAGMA user_version=1;
'''


class IdentityError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise IdentityError(422, '密码长度须为12至128个字符')
    salt = secrets.token_bytes(16)
    result = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return 'scrypt1$' + salt.hex() + '$' + result.hex()


def password_matches(password, encoded):
    if not isinstance(password, str) or len(password) > 128:
        return False
    try:
        version, salt, expected = encoded.split('$')
        if version != 'scrypt1':
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


# Same expensive password operation for unknown names, without storing their input.
DUMMY_HASH = 'scrypt1$' + '00' * 16 + '$' + '00' * 64


def session_digest(token):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise IdentityError(401, '请重新登录')
    return hashlib.sha256(token.encode()).hexdigest()


class IdentityStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self, write=False):
        # mode=rw deliberately refuses to silently create a missing identity DB.
        c = sqlite3.connect(self.path.resolve().as_uri() + '?mode=rw', uri=True, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.execute('PRAGMA foreign_keys=ON')
            if c.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise RuntimeError('Identity schema is not version 1')
            if write:
                c.execute('BEGIN IMMEDIATE')
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def initialize(self, username, password):
        self._username(username)
        encoded = password_hash(password)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation also refuses reinitialization of a populated store.
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        c = sqlite3.connect(self.path)
        try:
            c.execute('PRAGMA journal_mode=WAL')
            c.executescript(SCHEMA)
            aid = str(uuid.uuid4())
            c.execute('INSERT INTO accounts(id,username,password_hash,role,workspace,enabled,created_at) '
                      "VALUES (?,?,?,'manager',NULL,1,?)", (aid, username, encoded, int(time.time())))
            self._audit(c, aid, None, 'bootstrap', aid, 201)
            c.commit()
        finally:
            c.close()

    @staticmethod
    def _username(username):
        if not isinstance(username, str) or not re.fullmatch(r'[a-z][a-z0-9_.-]{2,39}', username):
            raise IdentityError(422, '账号名须为3至40位小写字母、数字或_.-，以字母开头')

    @staticmethod
    def _audit(c, actor, workspace, action, target, status):
        c.execute('INSERT INTO audit(created_at,actor,workspace,action,target,status) VALUES (?,?,?,?,?,?)',
                  (int(time.time()), actor, workspace, action, target, status))

    def audit(self, principal, action, target, status):
        with self.connect(True) as c:
            self._audit(c, principal['id'], principal['workspace'], action, target, status)

    @staticmethod
    def _principal(c, token):
        row = c.execute('SELECT ' + ','.join('a.' + f for f in PUBLIC.split(',')) +
                        ' FROM sessions s JOIN accounts a ON a.id=s.account_id '
                        'WHERE s.digest=? AND s.expires_at>? AND a.enabled=1',
                        (session_digest(token), int(time.time()))).fetchone()
        if row is None:
            raise IdentityError(401, '请重新登录')
        return dict(row)

    def principal(self, token):
        with self.connect() as c:
            return self._principal(c, token)

    def login(self, username, password):
        # Verification remains under the short, serialized identity transaction;
        # disabling/resetting a user cannot race a successful session issuance.
        with self.connect(True) as c:
            row = c.execute('SELECT * FROM accounts WHERE username=?', (username,)).fetchone()
            now = int(time.time())
            valid = password_matches(password, row['password_hash'] if row else DUMMY_HASH)
            valid = valid and row['enabled'] and row['locked_until'] <= now if row else False
            if not valid:
                if row:
                    failures = row['failures'] + 1
                    c.execute('UPDATE accounts SET failures=?,locked_until=? WHERE id=?',
                              (failures, now + 900 if failures >= 8 else row['locked_until'], row['id']))
                self._audit(c, row['id'] if row else None, row['workspace'] if row else None,
                            'login_denied', None, 401)
            else:
                c.execute('UPDATE accounts SET failures=0,locked_until=0 WHERE id=?', (row['id'],))
                c.execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
                # Bounded active sessions per account; retain the newest seven.
                c.execute('DELETE FROM sessions WHERE account_id=? AND digest NOT IN '
                          '(SELECT digest FROM sessions WHERE account_id=? ORDER BY created_at DESC,rowid DESC LIMIT 7)',
                          (row['id'], row['id']))
                token = secrets.token_urlsafe(32)
                c.execute('INSERT INTO sessions VALUES (?,?,?,?)',
                          (session_digest(token), row['id'], now + SESSION_SECONDS, now))
                self._audit(c, row['id'], row['workspace'], 'login', None, 200)
        if not valid:
            raise IdentityError(401, '账号或密码不正确，或账号暂不可用')
        return token

    def logout(self, token):
        with self.connect(True) as c:
            principal = self._principal(c, token)
            c.execute('DELETE FROM sessions WHERE digest=?', (session_digest(token),))
            self._audit(c, principal['id'], principal['workspace'], 'logout', None, 200)

    def _manager(self, c, token):
        p = self._principal(c, token)
        if p['role'] != 'manager':
            raise IdentityError(403, '仅账号维护员可管理账号')
        return p

    def create_account(self, token, username, password, role, workspace, workspaces):
        self._username(username)
        if role not in ROLES or workspace not in workspaces:
            raise IdentityError(422, '请选择有效的业务角色与工作区')
        encoded = password_hash(password)
        with self.connect(True) as c:
            actor = self._manager(c, token)
            if c.execute('SELECT count(*) FROM accounts WHERE enabled=1').fetchone()[0] >= 5:
                raise IdentityError(409, '有效人员账号已达到5个上限（含维护员）')
            aid = str(uuid.uuid4())
            try:
                c.execute('INSERT INTO accounts(id,username,password_hash,role,workspace,enabled,created_at) '
                          'VALUES (?,?,?,?,?,1,?)', (aid, username, encoded, role, workspace, int(time.time())))
            except sqlite3.IntegrityError:
                raise IdentityError(409, '账号名已存在') from None
            self._audit(c, actor['id'], workspace, 'account_created:' + role, aid, 201)
        return aid

    def update_account(self, token, aid, *, role=None, enabled=None, password=None):
        if role is not None and role not in ROLES:
            raise IdentityError(422, '业务角色无效')
        if enabled is not None and type(enabled) is not bool:
            raise IdentityError(422, '停用状态无效')
        encoded = password_hash(password) if password is not None else None
        with self.connect(True) as c:
            actor = self._manager(c, token)
            row = c.execute('SELECT * FROM accounts WHERE id=?', (aid,)).fetchone()
            if row is None:
                raise IdentityError(404, '账号不存在')
            # The sole maintenance account is recoverable via explicit offline
            # password rotation, and cannot be removed or turned into a tenant.
            if row['role'] == 'manager' and (role is not None or enabled is False):
                raise IdentityError(409, '不能停用账号维护员或赋予其业务角色')
            if enabled and not row['enabled'] and c.execute(
                    'SELECT count(*) FROM accounts WHERE enabled=1').fetchone()[0] >= 5:
                raise IdentityError(409, '有效人员账号已达到5个上限')
            c.execute('UPDATE accounts SET role=?,enabled=?,password_hash=?,failures=0,locked_until=0 WHERE id=?',
                      (role or row['role'], row['enabled'] if enabled is None else int(enabled),
                       encoded or row['password_hash'], aid))
            c.execute('DELETE FROM sessions WHERE account_id=?', (aid,))
            changes = []
            if role is not None:
                changes.append('role=' + row['role'] + '->' + role)
            if enabled is not None:
                changes.append('enabled=' + str(row['enabled']) + '->' + str(int(enabled)))
            if encoded is not None:
                changes.append('password=reset')
            self._audit(c, actor['id'], row['workspace'], 'account_updated:' + ','.join(changes), aid, 200)

    def accounts(self, token):
        with self.connect() as c:
            self._manager(c, token)
            return [dict(r) for r in c.execute('SELECT ' + PUBLIC + ' FROM accounts ORDER BY created_at,id')]

    def events(self, token, before=None, limit=100):
        with self.connect() as c:
            p = self._principal(c, token)
            if p['role'] not in {'admin', 'manager'}:
                raise IdentityError(403, '仅管理员可查看权限审计')
            return [dict(r) for r in c.execute('SELECT * FROM audit WHERE (? OR workspace=?) '
                    'AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?',
                    (p['role'] == 'manager', p['workspace'], before, before, min(max(limit, 1), 100)))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'recover-manager'])
    parser.add_argument('--manager', default='maintainer')
    args = parser.parse_args()
    store = IdentityStore(os.environ['RF_IDENTITY_DB'])
    password = os.environ.get('RF_BOOTSTRAP_PASSWORD')
    if password is None:
        import getpass
        password = getpass.getpass('New maintenance password: ')
    if args.action == 'init':
        store.initialize(args.manager, password)
    else:
        encoded = password_hash(password)
        with store.connect(True) as c:
            row = c.execute("SELECT id FROM accounts WHERE role='manager' AND username=?", (args.manager,)).fetchone()
            if row is None:
                raise RuntimeError('Unknown maintenance account')
            c.execute('UPDATE accounts SET password_hash=?,failures=0,locked_until=0 WHERE id=?', (encoded, row['id']))
            c.execute('DELETE FROM sessions WHERE account_id=?', (row['id'],))
            store._audit(c, row['id'], None, 'offline_recovery', row['id'], 200)
    print('Identity operation completed; no credentials printed')


if __name__ == '__main__':
    main()
