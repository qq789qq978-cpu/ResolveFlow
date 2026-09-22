"""Evaluation-only cost accounting around the existing ChatOpenAI HTTP transport."""
import json
import math
from pathlib import Path
import time

import httpx

MODEL='deepseek-flash'
BUDGET_MICROYUAN=10_000_000
MAX_REQUESTS=368
MAX_OUTPUT=1500
MAX_BODY_BYTES=120_000
# Official peak CNY / million tokens, treating all input as cache misses.
INPUT_RATE=2
OUTPUT_RATE=8


class BudgetStopped(RuntimeError):pass


class BudgetTransport(httpx.BaseTransport):
    def __init__(self,path,transport=None,limit=BUDGET_MICROYUAN):
        self.path=Path(path);self.inner=transport or httpx.HTTPTransport(retries=0)
        self.limit=limit;self.phase='unset';self.case_id='unset'
        self.rows=json.loads(self.path.read_text()) if self.path.exists() else []
        if any(r['status']!='settled' for r in self.rows):raise BudgetStopped('Unsettled request; do not automatically repeat')

    @property
    def charged(self):return sum(r['accounted_microyuan'] for r in self.rows)

    def save(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps(self.rows,indent=2)+'\n')
        temp.replace(self.path)

    def handle_request(self,request):
        if request.method!='POST' or str(request.url)!='https://api.deepseek.com/chat/completions':
            raise BudgetStopped('Endpoint outside the frozen evaluation scope')
        body=request.read();data=json.loads(body)
        if (len(body)>MAX_BODY_BYTES or data.get('model')!=MODEL or data.get('temperature')!=0
                or sum(k in data for k in ('max_tokens','max_completion_tokens'))!=1
                or data.get('max_tokens',data.get('max_completion_tokens'))!=MAX_OUTPUT or data.get('stream') is not False
                or data.get('thinking')!={'type':'disabled'}):
            raise BudgetStopped('Request outside the frozen model/token contract')
        # Byte count plus generous framing allowance is a conservative reservation,
        # not the provider tokenizer or a guaranteed billing quote.
        reserve=(len(body)+2048)*INPUT_RATE+MAX_OUTPUT*OUTPUT_RATE
        if len(self.rows)>=MAX_REQUESTS or self.charged+reserve>self.limit:
            raise BudgetStopped('Evaluation request/cost ceiling reached')
        row={'phase':self.phase,'case_id':self.case_id,'status':'pending',
             'reserved_microyuan':reserve,'accounted_microyuan':reserve}
        self.rows.append(row);self.save();start=time.monotonic()
        try:
            response=self.inner.handle_request(request);response.read()
            row['http_status']=response.status_code
            if response.status_code!=200:
                raise BudgetStopped('Provider HTTP failure; reserve retained, no automatic retry')
            result=response.json();usage=result.get('usage') or {}
            prompt,output=usage.get('prompt_tokens'),usage.get('completion_tokens')
            if type(prompt) is not int or type(output) is not int or prompt<0 or output<0:
                raise BudgetStopped('Missing usage; reserve retained')
            cost=prompt*INPUT_RATE+output*OUTPUT_RATE
            row.update(usage=usage,provider_model=result.get('model'),
                finish_reasons=[v.get('finish_reason') for v in result.get('choices',[])])
            if prompt>len(body)+2048 or output>MAX_OUTPUT or cost>reserve:
                row['accounted_microyuan']=max(cost,reserve)
                raise BudgetStopped('Provider exceeded reservation assumptions; stop')
            row.update(status='settled',accounted_microyuan=cost)
            return response
        except Exception:
            if row['status']=='pending':row['status']='uncertain_stop'
            raise
        finally:
            row['elapsed_ms']=round((time.monotonic()-start)*1000,3);self.save()

    def close(self):self.inner.close()

    def summary(self):
        times=sorted(r['elapsed_ms'] for r in self.rows if 'elapsed_ms' in r)
        return {'request_count':len(self.rows),'settled_requests':sum(r['status']=='settled' for r in self.rows),
            'uncertain_requests':sum(r['status']!='settled' for r in self.rows),
            'input_tokens':sum(r.get('usage',{}).get('prompt_tokens',0) for r in self.rows),
            'output_tokens':sum(r.get('usage',{}).get('completion_tokens',0) for r in self.rows),
            'peak_no_cache_estimate_cny':self.charged/1_000_000,'budget_cny':self.limit/1_000_000,
            'provider_models':sorted({r['provider_model'] for r in self.rows if r.get('provider_model')}),
            'request_latency_ms':{'p50':times[math.ceil(len(times)*0.5)-1] if times else None,
                'p95':times[math.ceil(len(times)*0.95)-1] if times else None,'max':max(times,default=None)},
            'billing_note':'Conservative peak/cache-miss estimate from provider usage, not an invoice; failed requests retain reservation.'}
