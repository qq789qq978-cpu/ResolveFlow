"""Frozen real-provider measurement in synthetic QA only; no main business writes."""
import argparse
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import httpx
from langchain_openai import ChatOpenAI
import engine
from evaluate_v3 import CASES
from grounding import check_grounding
from rag import read_index
from scripts.evaluate_rag_baseline import aggregate, score_case
from scripts.freeze_rag_split import digest,validate_split
from scripts.validate_rag_dataset import validate_dataset
from scripts.live_eval_budget import BudgetTransport,MODEL,MAX_OUTPUT


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');temp.replace(path)


def citations(case,state,catalog):
    """Exact reference/quote checks across actual multi-search union; not entailment."""
    proposal=state['proposal'];by_chunk={v['chunk_id']:(key,v) for key,v in catalog.items()}
    valid={}
    for hit in state['evidence']:
        pair=by_chunk.get(hit['chunk_id'])
        if pair and all(hit.get(out)==pair[1][field] for out,field in
            [('id','document_id'),('text','quote'),('source','source'),('version','version'),
             ('document_sha256','document_sha256'),('line_start','line_start'),('line_end','line_end')]):
            valid[hit['chunk_id']]=(pair[0],hit)
    refs=proposal['citations'];quotes=proposal['quotes'];verified=set();good_quotes=0
    for quote in quotes:
        found=valid.get(quote['chunk_id'])
        if found and quote['chunk_id'] in refs and len(quote['quote'].strip())>=12 and quote['quote'] in found[1]['text']:
            verified.add(found[0]);good_quotes+=1
    groups=case['expected']['required_evidence_groups']
    return {'references':len(refs),'valid_references':sum(cid in valid for cid in refs),
        'quotes':len(quotes),'valid_verbatim_quotes':good_quotes,
        'required_groups_covered_with_quotes':bool(groups) and all(set(group)&verified for group in groups),
        'evidence_union_size':len(state['evidence'])}


def fraction(n,d):return {'numerator':n,'denominator':d,'value':n/d if d else None}


def summarize(rows):
    ok=[r for r in rows if r['success']]
    answerable=[r for r in rows if r['answerability'] in ('supported','partial')]
    unsupported=[r for r in rows if r['answerability'] not in ('supported','partial')]
    ref=sum(r['citation_checks']['references'] for r in ok)
    quotes=sum(r['citation_checks']['quotes'] for r in ok)
    times=sorted(r['elapsed_ms'] for r in rows)
    return {'attempted':len(rows),'valid_outputs':len(ok),
        'case_latency_ms':{'p50':times[math.ceil(len(times)*0.5)-1] if times else None,
            'p95':times[math.ceil(len(times)*0.95)-1] if times else None,'max':max(times,default=None)},
        'errors':dict(Counter(r['error_type'] for r in rows if not r['success'])),
        'initial_retrieval':aggregate([r['initial_score'] for r in rows if r.get('initial_score')])['retrieval'],
        'model_actions':dict(Counter(r['state']['proposal']['action'] for r in ok)),
        'answerable_escalation':fraction(sum(r['success'] and r['state']['proposal']['action']=='escalate' for r in answerable),len(answerable)),
        'no_basis_escalation':fraction(sum(r['success'] and r['state']['proposal']['action']=='escalate' for r in unsupported),len(unsupported)),
        'no_basis_supported_action':fraction(sum(r['success'] and r['state']['proposal']['action']!='escalate' and r['state']['proposal']['evidence_status']=='supported' for r in unsupported),len(unsupported)),
        'reference_validity':fraction(sum(r['citation_checks']['valid_references'] for r in ok),ref),
        'verbatim_quote_validity':fraction(sum(r['citation_checks']['valid_verbatim_quotes'] for r in ok),quotes),
        'cited_required_with_verbatim_coverage':fraction(sum(r['success'] and r['citation_checks']['required_groups_covered_with_quotes'] for r in answerable),len(answerable)),
        'max_final_evidence_union':max((r['citation_checks']['evidence_union_size'] for r in ok),default=0),
        'live_grounding_usable':sum(r['success'] and r['live_grounding']['usable'] for r in rows),
        'claim_entailment':'not_measured','natural_language_refusal_correctness':'not_measured',
        'human_review':'pending','note':'Escalation is a structured action, not a semantic assessment of every sentence.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true');parser.add_argument('--private-dir',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True);args=parser.parse_args()
    if not args.live:parser.error('Use --live for this explicitly billable evaluation')
    if args.report.exists():parser.error('Keep prior reports; choose a new report path')
    assert os.environ.get('MODE')=='demo' and os.environ.get('RETRIEVAL_MODE')=='hybrid'
    assert os.environ['OPENAI_BASE_URL']=='https://api.deepseek.com' and os.environ['MODEL_NAME']==MODEL
    # A hardcoded public QA credential guards against accidentally using the main DB.
    assert os.environ['DATABASE_URL']=='postgresql://resolveflow:qa-step28-database@db:5432/resolveflow'
    assert args.private_dir.exists()
    data=json.loads((ROOT/'evals/rag/cases.v1.json').read_text(encoding='utf-8'))
    split=json.loads((ROOT/'evals/rag/split.v1.json').read_text(encoding='utf-8'))
    validate_dataset(data);validate_split(data,split)
    documents,chunks,release=read_index();assert release['valid'] and release['token']['generation']==1
    source_names=['engine.py','grounding.py','rag.py','semantic.py','mcp_gateway.py','mcp_server.py',
        'scripts/evaluate_live_rag.py','scripts/live_eval_budget.py','LIVE_MODEL_EVALUATION.md',
        'knowledge/governance.json','knowledge/release.json','requirements.lock']
    source_names += ['refund_policy.py','policy_governance.py','policy_releases.py','skill_loader.py','bm25_config.py']
    source_names += [p.relative_to(ROOT).as_posix() for p in sorted((ROOT/'skills').glob('*/SKILL.md'))]
    manifest={'model':MODEL,'dataset':digest(data),'split':digest(split),'release':release['token'],
        'sources':{p:hashlib.sha256((ROOT/p).read_text(encoding='utf-8-sig').encode()).hexdigest() for p in source_names}}
    manifest_path=args.private_dir/'manifest.json'
    if manifest_path.exists():assert json.loads(manifest_path.read_text())==manifest,'Frozen inputs changed; refuse resume'
    else:write(manifest_path,manifest)
    transport=BudgetTransport(args.private_dir/'usage-private.json')
    client=httpx.Client(transport=transport,timeout=30,trust_env=False,follow_redirects=False)
    model=ChatOpenAI(model=MODEL,base_url='https://api.deepseek.com',temperature=0,max_tokens=MAX_OUTPUT,
        timeout=30,max_retries=0,extra_body={'thinking':{'type':'disabled'}},http_client=client)
    original=engine.call_tools;rows=[];business=[];fatal=None;started_at=datetime.now(timezone.utc).isoformat()
    tasks=[('tuning',c) for c in data['cases'] if split['assignments'][c['id']]=='tuning']
    tasks += [('held_out',c) for c in data['cases'] if split['assignments'][c['id']]=='held_out']
    for phase,case in tasks:
        cid=case['id'];saved=args.private_dir/(cid+'.json')
        if saved.exists():rows.append(json.loads(saved.read_text(encoding='utf-8')));continue
        if any(r['case_id']==cid for r in transport.rows):raise RuntimeError('Partial case requires explicit recovery; never automatically rebill')
        initial=[]
        def observe(order,owner,calls):
            result=original(order,owner,calls)
            for (name,arguments),value in zip(calls,result):
                if name=='search_policy':
                    assert all(h['retrieval']=='hybrid' for h in value),'Unexpected retrieval fallback'
                    if not initial:initial.extend(value)
            return result
        row={'id':cid,'partition':phase,'answerability':case['expected']['answerability'],'success':False}
        transport.phase=phase;transport.case_id=cid;start=time.monotonic()
        try:
            with patch('engine.call_tools',observe):
                state=engine.Engine.investigate(SimpleNamespace(mode='live',model=model),
                    {'ticket':case['query'],'order_id':'RF-RAG-EVAL','owner':'demo'})
            row.update(success=True,state=state,citation_checks=citations(case,state,data['evidence_catalog']),
                live_grounding=check_grounding(state['proposal'],state['evidence'],mode='live',documents=documents,release=release))
        except Exception as error:
            row['error_type']=type(error).__name__
            # Structural model outputs can fail and remain a measured failure; HTTP,
            # timeout or budget uncertainty stops further billable requests.
            if not isinstance(error,ValueError):fatal=type(error).__name__
        row['elapsed_ms']=round((time.monotonic()-start)*1000,3)
        if initial:row['initial_score']=score_case(case,initial,{'citations':[],'action':'escalate'},data['evidence_catalog'])
        write(saved,row);rows.append(row)
        if len(rows)%5==0 or fatal:print(json.dumps({'completed_cases':len(rows),'phase':phase,'fatal':fatal,'cost':transport.summary()}),flush=True)
        if fatal:break
    if len(rows)==80 and not fatal:
        for index,(order,ticket,demo_expected) in enumerate(CASES,1):
            cid=f'BUSINESS-{index:02d}';saved=args.private_dir/(cid+'.json')
            if saved.exists():business.append(json.loads(saved.read_text(encoding='utf-8')));continue
            if any(r['case_id']==cid for r in transport.rows):raise RuntimeError('Partial business case; refuse automatic rebilling')
            transport.phase='business';transport.case_id=cid
            row={'id':cid,'order':order,'ticket':ticket,'demo_expected':demo_expected,
                 'live_expected':'escalated','expectation_reason':'Bundled policies are demo_fixture, not approved live policies','success':False}
            try:
                with tempfile.TemporaryDirectory() as directory:
                    e=engine.Engine(directory,mode='live',model=model)
                    try:
                        state=e.start(cid,ticket,order)['state']
                        row.update(success=True,state=state,actual=state['result']['status'],
                            sqlite_refunds=e.db.execute('SELECT count(*) FROM refunds').fetchone()[0])
                    finally:e.close()
            except Exception as error:
                row['error_type']=type(error).__name__
                if not isinstance(error,ValueError):fatal=type(error).__name__
            write(saved,row);business.append(row)
            print(json.dumps({'business_completed':len(business),'fatal':fatal,'cost':transport.summary()}),flush=True)
            if fatal:break
    client.close()
    partitions={p:summarize([r for r in rows if r['partition']==p]) for p in ('tuning','held_out')}
    # Private files retain original provider outputs for audit/recovery. Only tuning
    # details and aggregate held-out metrics enter the shareable repository report.
    tuning=[{k:v for k,v in r.items() if k!='initial_score'} for r in rows if r['partition']=='tuning']
    report={'completed':len(rows)==80 and len(business)==12 and not fatal,'fatal_error_type':fatal,
        'started_at':started_at,'finished_at':datetime.now(timezone.utc).isoformat(),
        'manifest':manifest,'model_parameters':{'temperature':0,'thinking':'disabled','max_output_tokens':MAX_OUTPUT,'max_retries':0},
        'cost':transport.summary(),'splits':partitions,'tuning_results':tuning,'held_out_details':'withheld_by_protocol',
        'business_results':business,'business_expected_matches':sum(r['success'] and r['actual']==r['live_expected'] for r in business),
        'business_refunds':sum(r.get('sqlite_refunds',0) for r in business),
        'method':'Actual Engine.investigate, actual MCP, isolated PostgreSQL demo corpus, local E5/RRF, real DeepSeek API. Business Engine uses temporary SQLite checkpoints.',
        'policy_mode':'demo fixtures retained for retrieval measurement; final authorization evaluated with live mode and remains blocked',
        'quality_gate':'measurement completed is not a production quality pass',
        'limitations':['80 model-authored labels; human review pending; not externally independent.',
            'Model may request extra searches; initial retrieval top4, final evidence union up to seven.',
            'Reasons are internal unvalidated proposals, not user-visible approved answers.',
            'Exact quote matching does not measure semantic entailment, completeness or all false claims.',
            'deepseek-flash is a provider alias; no immutable hosted model revision is available.',
            'Cost is a conservative usage estimate, not the supplier invoice.']}
    write(args.report,report)
    print(json.dumps({k:v for k,v in report.items() if k in ('completed','fatal_error_type','cost','splits','business_expected_matches','business_refunds')}),flush=True)
    if not report['completed']:raise SystemExit(1)


if __name__=='__main__':main()
