import os
import psycopg
from psycopg.rows import dict_row
import pytest
from capacity import execution_slot
from test_jobs import system
import jobs

pytestmark=pytest.mark.skipif(os.getenv('RUN_PG_TESTS')!='1',reason='isolated PostgreSQL only')

@pytest.mark.parametrize('workspace,slots',[('alpha',2),('beta',1)])
def test_slots_bound_extra_workers_and_release_on_connection_death(system,monkeypatch,workspace,slots):
    store,engine,client=system
    monkeypatch.setenv('RF_CAPACITY_ENABLED','1');monkeypatch.setenv('RF_WORKSPACE_ID',workspace);monkeypatch.setenv('RF_EXECUTION_SLOTS',str(slots))
    connections=[psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) for _ in range(slots+1)]
    try:
        assert all(execution_slot(c) for c in connections[:-1])
        assert not execution_slot(connections[-1])
        connections[0].close()
        assert execution_slot(connections[-1])
    finally:
        for c in connections:c.close()

def test_full_slots_do_not_claim_or_drop_queued_job(system,monkeypatch):
    store,engine,client=system
    import uuid
    rid=str(uuid.uuid4());jobs.enqueue(store,rid,'refund','RF-1002','demo',None,'test')
    monkeypatch.setenv('RF_CAPACITY_ENABLED','1');monkeypatch.setenv('RF_WORKSPACE_ID','beta');monkeypatch.setenv('RF_EXECUTION_SLOTS','1')
    with store.connect() as blocker:
        assert execution_slot(blocker)
        assert not jobs.process_one(store,engine,rid)
        assert store.get(rid)['status']=='queued'
    assert jobs.process_one(store,engine,rid)
    assert store.get(rid)['status']=='auto_rejected'
