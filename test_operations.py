"""Small API-independent checks; PostgreSQL integration is in test_jobs.py."""
from conflicts import detect_conflicts

def test_explicit_conflicts():
    order={'used':False,'days':3}
    assert len(detect_conflicts('商品已使用，签收超过七天，申请退款',order))==2
    assert not detect_conflicts('商品未使用，签收3天',order)
