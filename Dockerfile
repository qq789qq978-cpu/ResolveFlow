FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock
COPY operations.py auth.py jobs.py worker.py worker_health.py engine.py grounding.py policy_governance.py policy_releases.py storage.py refund_policy.py conflicts.py model_config.py support_data.py mcp_server.py mcp_gateway.py skill_loader.py rag.py bm25_config.py ./
COPY knowledge ./knowledge
COPY semantic.py embedding_contract.py ./
COPY scripts/build_vector_index.py ./scripts/build_vector_index.py
COPY skills ./skills
COPY frontend/dist ./frontend/dist
RUN useradd --create-home appuser && mkdir /data && chown appuser /data
USER appuser
ENV DATA_DIR=/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "operations:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
