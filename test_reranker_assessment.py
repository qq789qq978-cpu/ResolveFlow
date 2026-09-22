"""Analytical checks for the label oracle, not a reranker model benchmark."""
from scripts.assess_reranker import best_order


def fixture():
    catalog={}
    hits=[]
    for index in range(4):
        eid=str(index)
        source={'chunk_id':eid,'document_id':'document','quote':'source '+eid,
            'source':'fixture.md','version':'1','document_sha256':'fixture',
            'line_start':index+1,'line_end':index+1}
        catalog[eid]=source
        hits.append({**source,'id':'document','text':source['quote']})
    return catalog,hits


def case(groups):
    return {'id':'synthetic','category':'synthetic','expected':{'answerability':'supported',
        'required_evidence_groups':groups,'optional_evidence':[],'guardrail_evidence':[]}}


def test_shared_evidence_can_cover_two_groups_in_one_position():
    catalog,hits=fixture()
    result,count=best_order(case([['1','3'],['2','3']]),hits,{'citations':[],'action':'escalate'},catalog)
    assert count==24
    assert result['hits'][0]['chunk_id']=='3'
    assert result['complete_evidence_rr']==1  # Not 1 / number_of_groups.
    assert result['all_required'] and result['context_hits']==3


def test_oracle_cannot_recover_an_absent_group_or_accept_corrupt_source():
    catalog,hits=fixture()
    hits[3]['text']='tampered'
    result,_=best_order(case([['1'],['3']]),hits,{'citations':[],'action':'escalate'},catalog)
    assert not result['all_required'] and result['complete_evidence_rr']==0
    assert result['group_recall']==.5 and result['source_valid_hits']==3
    assert {h['chunk_id'] for h in result['hits']}=={'0','1','2','3'}
