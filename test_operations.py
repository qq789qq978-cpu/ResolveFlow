"""Small API-independent checks; PostgreSQL integration is in test_jobs.py."""
from conflicts import detect_conflicts

def test_explicit_conflicts():
    order={'used':False,'days':3}
    assert len(detect_conflicts('商品已使用，签收超过七天，申请退款',order))==2
    assert not detect_conflicts('商品未使用，签收3天',order)

def test_negative_usage_claims_and_incomplete_facts():
    for phrase in ('没有使用过','从未使用过','没用过','未使用'):
        assert not detect_conflicts(phrase,{'used':False,'days':3})
        assert detect_conflicts(phrase,{'used':True,'days':3})
    assert not detect_conflicts('已使用，签收超过七天',{'status':'delivered'})
