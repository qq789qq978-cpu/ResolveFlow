import uuid
import pytest
from mcp_gateway import call_tools
from skill_loader import discover, select_skill
from engine import Engine
from test_engine import FakeModel

def test_real_mcp_roundtrip_and_scope():
    policies, order = call_tools("RF-1001", "demo", [("search_policy", {"query": "退款"}), ("lookup_order", {})])
    assert any(p["id"] == "refund-v2" for p in policies)
    assert order["amount"] == 29900
    assert call_tools("RF-1001", "other", [("lookup_order", {})]) == [{}]

def test_mcp_unknown_tool_rejected():
    with pytest.raises(Exception):
        call_tools("RF-1001", "demo", [("execute_refund", {})])

def test_mcp_passes_local_retrieval_configuration(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('RETRIEVAL_MODE', 'hybrid')
    monkeypatch.setenv('SEMANTIC_WEIGHT', '0.25')
    # Endpoint rejection is observable before network access or loading weights.
    # Without configuration forwarding this child silently returns plain BM25.
    monkeypatch.setenv('EMBEDDING_URL', 'https://example.com')
    hits = call_tools('RF-1001', 'demo', [('search_policy', {'query': '退款'})])[0]
    assert hits and all(h['retrieval_fallback']['reason'] == 'vector_database_not_configured' for h in hits)

def test_mcp_error_propagates():
    with pytest.raises(Exception):
        call_tools("RF-1001", "demo", [("search_policy", {"query": "x" * 4001})])

def test_mcp_handler_supports_bounded_async_retriever(monkeypatch):
    import asyncio
    import mcp_server
    from semantic import _run
    async def fetch():
        await asyncio.sleep(0)
        return [{'chunk_id': 'fixture', 'retrieval': 'hybrid'}]
    monkeypatch.setattr(mcp_server, 'retrieve', lambda query: _run(fetch()))
    assert asyncio.run(mcp_server.search_policy('fixture'))[0]['retrieval'] == 'hybrid'

def test_unavailable_mcp_does_not_fallback(tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("Unavailable MCP")
    monkeypatch.setattr("engine.call_tools", fail)
    engine = Engine(str(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="Unavailable MCP"):
            engine.start(str(uuid.uuid4()), "退款", "RF-1001")
        assert engine.db.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] == 0
    finally:
        engine.close()

@pytest.mark.parametrize("ticket,name", [("我要退款", "refund-handling"), ("查询物流", "shipping-handling"), ("天气", "support-triage"), ("../../etc/passwd", "support-triage")])
def test_skill_selection(ticket, name):
    assert len(discover()) == 3
    selected = select_skill(ticket)
    assert selected["name"] == name
    assert len(selected["sha256"]) == 64

def test_skill_reaches_model(tmp_path):
    class InspectModel(FakeModel):
        def invoke(self, messages):
            assert "# 退款处理" in messages[0].content
            assert "# 物流处理" not in messages[0].content
            return super().invoke(messages)
    engine = Engine(str(tmp_path), "live", InspectModel())
    try:
        result = engine.start(str(uuid.uuid4()), "我要退款", "RF-1004")
        assert result["state"]["skill"]["name"] == "refund-handling"
        assert not result["pending"]
        assert 'policy_demo_only' in result['state']['result']['grounding']['errors']
        assert any(t.get("transport") == "mcp-stdio" for t in result["state"]["trace"])
    finally:
        engine.close()
