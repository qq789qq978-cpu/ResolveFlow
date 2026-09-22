FROM python:3.12-slim
WORKDIR /app
COPY embedding/requirements.lock /app/embedding/requirements.lock
RUN pip install --no-cache-dir torch==2.8.0+cpu --index-url https://download.pytorch.org/whl/cpu && pip install --no-cache-dir -r /app/embedding/requirements.lock
COPY embedding_service.py embedding_contract.py ./
COPY scripts/download_embeddings.py ./scripts/download_embeddings.py
COPY embedding/model-manifest.json ./embedding/model-manifest.json
RUN useradd --create-home appuser
USER appuser
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=2
HEALTHCHECK --interval=5s --timeout=3s --start-period=90s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')"
CMD ["uvicorn","embedding_service:app","--host","0.0.0.0","--port","8080","--workers","1","--no-access-log"]
