import uuid
import pytest
from langchain_core.messages import AIMessage
from engine import Engine, Proposal

def uid():
    return str(uuid.uuid4())

def test_restart_and_approval(tmp_path):
    e = Engine(str(tmp_path))
    run = uid()
    assert e.start(run, "申请退款", "RF-1004")["pending"]
    assert e.db.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 0
    e.close()
    e = Engine(str(tmp_path))
    assert e.resume(run, True)["state"]["result"]["status"] == "refunded"
    with pytest.raises(ValueError):
        e.resume(run, True)
    second = uid()
    e.start(second, "退款", "RF-1004")
    assert e.resume(second, True)["state"]["result"]["status"] == "already_refunded"
    assert e.db.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 1
    e.close()

@pytest.mark.parametrize("ticket,order,status", [
    ("申请退款", "RF-1002", "auto_rejected"),
    ("申请退款", "RF-9999", "escalated"),
    ("查询物流", "RF-1003", "answered"),
    ("请写一首诗", "RF-1004", "escalated"),
    ("退款", "RF-1003", "escalated"),
])
def test_routes(tmp_path, ticket, order, status):
    e = Engine(str(tmp_path))
    result = e.start(uid(), ticket, order)
    assert not result["pending"]
    assert result["state"]["result"]["status"] == status
    e.close()

def test_rejection_and_owner(tmp_path):
    e = Engine(str(tmp_path))
    run = uid()
    e.start(run, "退款", "RF-1004")
    assert e.resume(run, False)["state"]["result"]["status"] == "rejected"
    assert e.start(uid(), "退款", "RF-1004", "other")["state"]["result"]["status"] == "escalated"
    assert e.db.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 0
    e.close()

def test_forged_evidence(tmp_path):
    e = Engine(str(tmp_path))
    result = e.validate({"proposal": {"action": "refund", "citations": ["invented"], "reason": "fake"}, "order": {"status": "delivered", "days": 1, "used": False}, "evidence": [], "trace": []})
    assert result["validated"] is False
    e.close()

class FakeModel:
    """Verifies live orchestration contract without claiming real-model quality."""
    def bind_tools(self, tools):
        self.calls = 0
        return self

    def invoke(self, messages):
        self.calls += 1
        return AIMessage(content="", tool_calls=[{"name": "search_policy", "args": {"query": "退款"}, "id": f"call-{self.calls}"}]) if self.calls == 1 else AIMessage(content="调查完成")

    def with_structured_output(self, schema):
        class Structured:
            def invoke(self, messages):
                return Proposal(action="refund", reason="符合政策", citations=["refund-v2"])
        return Structured()

def test_live_contract(tmp_path):
    e = Engine(str(tmp_path), "live", FakeModel())
    result = e.start(uid(), "申请退款", "RF-1004")
    assert result["pending"]
    assert any(t.get("tool") == "search_policy" for t in result["state"]["trace"])
    e.close()

def test_final_reason_matches_approval_result(tmp_path):
    e=Engine(str(tmp_path))
    try:
        rid=uid()
        e.start(rid,'退款','RF-1004')
        result=e.resume(rid,False)['state']['result']
        assert result['status']=='rejected'
        assert result['reason']==result['response']=='人工审批拒绝退款。'
    finally:
        e.close()
