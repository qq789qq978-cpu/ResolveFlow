from copy import deepcopy
from scripts.evaluate_live_rag import citations, summarize


def test_quote_must_match_actual_retrieved_source_and_cover_required_group():
    text='The complete original policy passage.'
    catalog={'R1':{'chunk_id':'chunk1','document_id':'doc1','quote':text,'source':'refund.md',
        'version':'1','document_sha256':'a'*64,'line_start':1,'line_end':3}}
    source=catalog['R1']
    hit={k:v for k,v in source.items() if k not in ('document_id','quote')}
    hit.update(id=source['document_id'],text=text)
    state={'evidence':[hit],'proposal':{'citations':['chunk1'],'quotes':[{'chunk_id':'chunk1','quote':text}]}}
    case={'expected':{'required_evidence_groups':[['R1']]}}
    assert citations(case,state,catalog)['required_groups_covered_with_quotes']
    bad=deepcopy(state);bad['proposal']['quotes'][0]['quote']='A fabricated policy statement.'
    assert not citations(case,bad,catalog)['required_groups_covered_with_quotes']
    bad=deepcopy(state);bad['evidence'][0]['document_sha256']='b'*64
    assert citations(case,bad,catalog)['valid_references']==0
    bad=deepcopy(state);bad['proposal']['citations']=[]
    assert citations(case,bad,catalog)['valid_verbatim_quotes']==0


def test_failed_outputs_stay_in_denominator_and_are_not_counted_as_refusals():
    rows=[{'success':False,'answerability':status,'error_type':'ValueError','elapsed_ms':12}
          for status in ('supported','unsupported')]
    result=summarize(rows)
    assert result['valid_outputs']==0 and result['attempted']==2
    assert result['no_basis_escalation']=={'numerator':0,'denominator':1,'value':0}
    assert result['cited_required_with_verbatim_coverage']=={'numerator':0,'denominator':1,'value':0}
    assert result['verbatim_quote_validity']['value'] is None
    assert result['claim_entailment']=='not_measured'
