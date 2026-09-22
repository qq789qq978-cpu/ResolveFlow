import json
import httpx
import pytest
from scripts.live_eval_budget import BudgetTransport,BudgetStopped


def request(**overrides):
    body={'model':'deepseek-flash','temperature':0,'max_tokens':1500,'stream':False,
          'thinking':{'type':'disabled'},'messages':[{'role':'user','content':'Synthetic fixture'}]}
    body.update(overrides)
    return httpx.Request('POST','https://api.deepseek.com/chat/completions',json=body)


def test_budget_refuses_before_sending_and_accounts_real_usage(tmp_path):
    calls=[]
    def reply(req):
        calls.append(req)
        return httpx.Response(200,json={'model':'deepseek-flash','usage':{'prompt_tokens':100,'completion_tokens':50},'choices':[]})
    t=BudgetTransport(tmp_path/'ledger.json',httpx.MockTransport(reply),limit=1)
    with pytest.raises(BudgetStopped,match='ceiling'):t.handle_request(request())
    assert not calls
    t.limit=100_000;t.handle_request(request())
    assert t.charged==600 and len(calls)==1


@pytest.mark.parametrize('payload',[{}, {'model':'deepseek-v4-pro'}, {'max_tokens':1501}])
def test_endpoint_or_model_changes_never_send(tmp_path,payload):
    def fail(req):pytest.fail('Out-of-scope request was sent')
    t=BudgetTransport(tmp_path/'ledger.json',httpx.MockTransport(fail))
    req=request(**payload) if payload else httpx.Request('POST','https://example.com/chat/completions',json={})
    with pytest.raises(BudgetStopped):t.handle_request(req)


def test_unknown_usage_retains_full_reservation(tmp_path):
    t=BudgetTransport(tmp_path/'ledger.json',httpx.MockTransport(lambda req:httpx.Response(200,json={})))
    with pytest.raises(BudgetStopped,match='Missing usage'):t.handle_request(request())
    assert t.rows[0]['status']=='uncertain_stop'
    assert t.charged==t.rows[0]['reserved_microyuan']>0
    with pytest.raises(BudgetStopped,match='Unsettled'):BudgetTransport(tmp_path/'ledger.json')


def test_pending_crash_refuses_automatic_repeat(tmp_path):
    p=tmp_path/'ledger.json';p.write_text(json.dumps([{'status':'pending','accounted_microyuan':100}]))
    with pytest.raises(BudgetStopped,match='Unsettled'):BudgetTransport(p,httpx.MockTransport(lambda req:None))
