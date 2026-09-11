import os
from langchain_openai import ChatOpenAI

def build_model():
    if not os.getenv('OPENAI_API_KEY'):
        raise ValueError('MODE=live requires OPENAI_API_KEY in local .env')
    base_url = os.getenv('OPENAI_BASE_URL', 'https://api.deepseek.com')
    kwargs = dict(model=os.getenv('MODEL_NAME','deepseek-flash'), base_url=base_url,
                  temperature=0, max_tokens=1500, timeout=30, max_retries=1)
    if 'api.deepseek.com' in base_url:
        kwargs['extra_body'] = {'thinking': {'type': 'disabled'}}
    return ChatOpenAI(**kwargs)
