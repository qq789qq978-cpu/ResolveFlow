"""One offline CPU encoder, no database credentials and no request-content logs."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import threading
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from embedding_contract import CONTRACT, CONTRACT_ID, validate_vector
from scripts.download_embeddings import verify


class Input(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['query','passage']
    text: str=Field(min_length=1,max_length=12000)
    title: str=Field(default='',max_length=500)


def load_encoder():
    import torch
    from transformers import AutoModel, AutoTokenizer
    path=Path(os.environ.get('EMBEDDING_MODEL_DIR','/model'))
    manifest=json.loads(Path('/app/embedding/model-manifest.json').read_text())
    if manifest['revision']!=CONTRACT['revision'] or any(not verify(path/f['name'],f) for f in manifest['files']):
        raise RuntimeError('Model artifact verification failed')
    torch.set_num_threads(2);torch.set_num_interop_threads(1)
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
    model=AutoModel.from_pretrained(path,local_files_only=True,trust_remote_code=False,use_safetensors=True)
    model.eval()
    def encode(value):
        text=('query: '+value.text if value.kind=='query' else 'passage: '+value.title+'\n'+value.text)
        batch=tokenizer(text,return_tensors='pt',truncation=False)
        length=batch['input_ids'].shape[1]
        if length>512:raise ValueError('token_limit')
        with torch.inference_mode():
            hidden=model(**batch).last_hidden_state
            mask=batch['attention_mask'].unsqueeze(-1)
            pooled=(hidden*mask).sum(dim=1)/mask.sum(dim=1)
            vector=torch.nn.functional.normalize(pooled,p=2,dim=1)[0].tolist()
        return validate_vector(vector),length
    return encode


@asynccontextmanager
async def lifespan(app):
    start=time.monotonic()
    app.state.encoder=load_encoder()
    app.state.load_ms=round((time.monotonic()-start)*1000,3)
    yield


app=FastAPI(lifespan=lifespan)
gate=threading.Lock()


@app.get('/health')
def health():return {'ready':True,'contract':CONTRACT_ID,'load_ms':app.state.load_ms}


@app.post('/encode')
def encode(value:Input):
    if not gate.acquire(blocking=False):raise HTTPException(503,'encoder_busy')
    try:
        start=time.monotonic()
        vector,length=app.state.encoder(value)
        return {'contract':CONTRACT_ID,'vector':vector,'tokens':length,'elapsed_ms':round((time.monotonic()-start)*1000,3)}
    except ValueError as error:
        raise HTTPException(422,str(error)) from None
    finally:gate.release()
