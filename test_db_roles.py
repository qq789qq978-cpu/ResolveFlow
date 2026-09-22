"""Credential bootstrap preserves local configuration; rejects unsafe input."""
from pathlib import Path
import pytest
from db_roles import validate_names,validate_passwords,ROLES
from scripts import setup_db_credentials


@pytest.mark.parametrize('schema',['public;drop schema public','pg_catalog','information_schema'])
def test_unsafe_schema(schema):
    with pytest.raises(ValueError):validate_names(schema,ROLES)


@pytest.mark.parametrize('passwords',[
    {'migrator':'same'*8,'app':'same'*8,'readonly':'same'*8},
    {'migrator':'a'*24,'app':'short','readonly':'c'*24},
    {'migrator':'a'*24,'app':'bad:@/'*5,'readonly':'c'*24},
    {'migrator':'a'*24,'app':'b'*24},
])
def test_invalid_credentials(passwords):
    with pytest.raises(ValueError):validate_passwords(passwords)


def test_append_only_credential_setup(tmp_path,monkeypatch,capsys):
    (tmp_path/'scripts').mkdir()
    monkeypatch.setattr(setup_db_credentials,'__file__',str(tmp_path/'scripts/setup_db_credentials.py'))
    path=tmp_path/'.env';original='# local\nMODE=demo\nRF_APP_PASSWORD='+('a'*24)+'\n'
    path.write_text(original)
    setup_db_credentials.main();after=path.read_text()
    assert after.startswith(original)
    values=dict(line.split('=',1) for line in after.splitlines() if '=' in line)
    passwords={k:values['RF_'+k.upper()+'_PASSWORD'] for k in ROLES}
    validate_passwords(passwords)
    setup_db_credentials.main();assert path.read_text()==after
    output=capsys.readouterr().out
    assert not any(p in output for p in passwords.values())


def test_admin_password_reuse_refused_before_connect():
    from db_roles import provision
    password='admin-password-1234567890123'
    with pytest.raises(ValueError,match='administrator password'):
        provision('postgresql://admin:'+password+'@invalid/db',
                  {'migrator':password,'app':'b'*24,'readonly':'c'*24})
