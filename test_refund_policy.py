import pytest
import uuid
from refund_policy import decide
from engine import Engine

@pytest.mark.parametrize('days,used,expected',[(3,False,'auto_approved'),(7,False,'auto_approved'),(8,False,'awaiting_approval'),(3,True,'awaiting_approval'),(8,True,'auto_rejected')])
def test_decision_table(days,used,expected):
    result=decide({'status':'delivered','days':days,'used':used,'amount':100},True,[])
    assert result['route']==expected
    assert len(result['checks'])==2

@pytest.mark.parametrize('change', [{'status':'shipping'},{'used':None},{'days':None},{'amount':0},{'days':-1}])
def test_unknown_is_not_false(change):
    order={'status':'delivered','days':3,'used':False,'amount':100,**change}
    assert decide(order,True,[])['route']=='escalated'

def test_evidence_and_conflict_gate():
    order={'status':'delivered','days':3,'used':False,'amount':100}
    assert decide(order,False,[])['route']=='escalated'
    assert decide(order,True,['描述冲突'])['route']=='escalated'

def test_auto_refund_and_replay(tmp_path):
    e=Engine(str(tmp_path))
    try:
        first=e.start(str(uuid.uuid4()),'申请退款','RF-1001')
        assert not first['pending']
        assert first['state']['result']['status']=='refunded'
        assert first['state']['result']['decision_source']=='automatic'
        assert not any(t['node']=='approval' for t in first['state']['trace'])
        second=e.start(str(uuid.uuid4()),'申请退款','RF-1001')
        assert second['state']['result']['status']=='already_refunded'
        assert e.db.execute('SELECT COUNT(*) FROM refunds').fetchone()[0]==1
        rejected=e.start(str(uuid.uuid4()),'申请退款','RF-1002')
        assert rejected['state']['result']['status']=='auto_rejected'
        assert not rejected['pending']
        assert e.db.execute('SELECT COUNT(*) FROM refunds').fetchone()[0]==1
    finally:e.close()
