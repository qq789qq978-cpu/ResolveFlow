"""Read-only restore verifier including sequence and checkpoint edge cases."""
import os

import jobs
import psycopg
import pytest

from db_migrate import migrate
from scripts.backup_snapshot import snapshot_session
from scripts.restore_validation import validate_restore
from test_migrations_pg import database
from test_jobs import system,submit

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='Isolated PostgreSQL required')


def manifest(dsn):
    with snapshot_session(dsn,public_only=False) as metadata:
        return {'snapshot':metadata}


def test_core_restore_with_empty_tables_and_uncalled_sequences(database):
    dsn,_=database
    migrate(dsn,demo=True)
    expected=manifest(dsn)
    result=validate_restore(dsn,expected,public_only=False)
    assert result['matched_tables']==20 and result['indexes_valid']
    assert len(result['sequences'])==3 and all(s['safe'] for s in result['sequences'].values())
    assert any(not s['is_called'] for s in result['sequences'].values())
    assert manifest(dsn)['snapshot']['tables']==expected['snapshot']['tables']


def test_sequence_behind_maximum_refused_without_auto_setval(database):
    dsn,_=database
    migrate(dsn,demo=True)
    expected=manifest(dsn)
    with psycopg.connect(dsn) as c:c.execute("SELECT setval('rf_policy_events_id_seq',1,false)")
    with pytest.raises(ValueError,match='Unsafe restored sequence'):
        validate_restore(dsn,expected,public_only=False)
    with psycopg.connect(dsn) as c:
        assert c.execute('SELECT last_value,is_called FROM rf_policy_events_id_seq').fetchone()==(1,False)


def test_sequence_gap_is_safe_and_not_rewound_to_backup_observation(database):
    dsn,_=database
    migrate(dsn,demo=True)
    expected=manifest(dsn)
    with psycopg.connect(dsn) as c:c.execute("SELECT setval('rf_policy_events_id_seq',100,true)")
    result=validate_restore(dsn,expected,public_only=False)
    assert result['sequences']['rf_policy_events_id_seq']['next_value']==101
    with psycopg.connect(dsn) as c:
        assert c.execute('SELECT last_value,is_called FROM rf_policy_events_id_seq').fetchone()==(100,True)


@pytest.mark.parametrize('change',['row','fingerprint_version','checkpoint_version'])
def test_data_and_version_mismatch_refused(database,change):
    dsn,_=database
    migrate(dsn,demo=True)
    expected=manifest(dsn)
    if change=='row':
        with psycopg.connect(dsn) as c:c.execute('UPDATE rf_orders SET amount=amount+1')
    elif change=='fingerprint_version':expected['snapshot']['fingerprint_format']='unknown'
    else:expected['snapshot']['checkpoint_migrations']=[999]
    with pytest.raises(ValueError):validate_restore(dsn,expected,public_only=False)


def test_real_uuid_pending_checkpoint_readable_and_no_writes(system):
    store,engine,client=system
    rid=submit(client)
    assert jobs.process_one(store,engine,rid)
    expected=manifest(store.url)
    result=validate_restore(store.url,expected,public_only=False)
    assert result['readable_checkpoints'][0]['run_id']==rid
    assert result['readable_checkpoints'][0]['status']=='awaiting_approval'
    assert manifest(store.url)['snapshot']['tables']==expected['snapshot']['tables']


def test_saved_business_state_without_checkpoint_rejected(system):
    store,engine,client=system
    rid=submit(client)
    assert jobs.process_one(store,engine,rid)
    with psycopg.connect(store.url) as c:c.execute('DELETE FROM checkpoints')
    expected=manifest(store.url)
    with pytest.raises(ValueError,match='no readable checkpoint'):
        validate_restore(store.url,expected,public_only=False)
