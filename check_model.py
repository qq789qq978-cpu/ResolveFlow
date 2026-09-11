"""Prints connectivity metadata only; never prints credentials."""
import json
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI
import os

load_dotenv(Path(__file__).with_name('.env'), encoding='utf-8-sig')
client = OpenAI(api_key=os.environ['OPENAI_API_KEY'],base_url=os.environ['OPENAI_BASE_URL'],timeout=30,max_retries=0)
if __name__ == '__main__':
    try:
        models=[m.id for m in client.models.list().data]
        print(json.dumps({'available_models':models}))
    except Exception as error:
        print(json.dumps({'error_type':type(error).__name__,'status':getattr(error,'status_code',None)}))
        raise SystemExit(1)
