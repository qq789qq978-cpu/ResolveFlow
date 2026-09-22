"""Versioned local encoder identity shared by the service and retrieval client."""
import hashlib
import json
import math

MODEL='intfloat/multilingual-e5-small'
REVISION='614241f622f53c4eeff9890bdc4f31cfecc418b3'
DIMENSIONS=384
CONTRACT={'model':MODEL,'revision':REVISION,'dimensions':DIMENSIONS,'dtype':'float32',
          'pooling':'attention_mask_mean','normalization':'l2','max_tokens':512,
          'query_template':'query: {text}','passage_template':'passage: {title}\n{text}','schema':1}
CONTRACT_ID=hashlib.sha256(json.dumps(CONTRACT,sort_keys=True).encode()).hexdigest()


def validate_vector(vector):
    if not isinstance(vector,list) or len(vector)!=DIMENSIONS:
        raise ValueError('vector_dimension')
    if any(type(x) not in (int,float) or not math.isfinite(x) for x in vector):
        raise ValueError('vector_nonfinite')
    if not .999 <= math.sqrt(sum(x*x for x in vector)) <= 1.001:
        raise ValueError('vector_norm')
    return vector


def vector_digest(vector):
    # PostgreSQL vector uses float32; hash the same byte representation on both sides.
    import struct
    return hashlib.sha256(struct.pack('<'+str(DIMENSIONS)+'f',*validate_vector(vector))).hexdigest()
