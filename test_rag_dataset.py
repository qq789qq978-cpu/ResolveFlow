"""Label-data guardrails: reject corruption, corpus drift and unsupported review claims."""
import json
from pathlib import Path
import shutil
import pytest

from scripts.validate_rag_dataset import DATASET, validate_dataset


@pytest.fixture
def dataset():
    return json.loads(DATASET.read_text(encoding='utf-8'))


def test_frozen_candidates_have_sources_but_no_quality_or_human_claim(dataset):
    report = validate_dataset(dataset)
    assert report['cases'] == 80 and report['evidence_units'] == 7
    assert report['review']['human_pending'] == 80
    assert report['retrieval_executed'] is False and report['split'] == 'unassigned'


@pytest.mark.parametrize('field,value', [
    ('quote','不存在的政策原文'), ('line_start',999), ('version','999'), ('document_sha256','0'*64)])
def test_reject_invented_or_mislocated_evidence(dataset,field,value):
    dataset['evidence_catalog']['R1'][field] = value
    with pytest.raises(ValueError, match='quote, location or version'):
        validate_dataset(dataset)


def test_reject_corpus_changed_since_annotation(dataset,tmp_path):
    source = Path(__file__).parent/'knowledge'
    shutil.copytree(source,tmp_path/'knowledge')
    p = tmp_path/'knowledge/refund.md'
    p.write_text(p.read_text(encoding='utf-8').replace('7天','14天'),encoding='utf-8')
    with pytest.raises(ValueError,match='Corpus snapshot drift|governance hash'):
        validate_dataset(dataset,tmp_path/'knowledge')


def test_reject_duplicate_query_even_with_spaces_and_punctuation(dataset):
    dataset['cases'][1]['query'] = '  '+dataset['cases'][0]['query']+'！！'
    with pytest.raises(ValueError,match='duplicate normalized query'):
        validate_dataset(dataset)


def test_reject_untraceable_evidence_reference(dataset):
    dataset['cases'][0]['expected']['required_evidence_groups'] = [['NONEXISTENT']]
    with pytest.raises(ValueError,match='unknown evidence reference'):
        validate_dataset(dataset)


def test_reject_evidence_removed_from_supported_answer(dataset):
    dataset['cases'][0]['expected']['required_evidence_groups'] = []
    with pytest.raises(ValueError,match='answerability/evidence mismatch'):
        validate_dataset(dataset)


def test_reject_promoting_context_to_an_unsupported_answer(dataset):
    case = next(c for c in dataset['cases'] if c['expected']['answerability']=='unsupported')
    case['expected']['required_evidence_groups'] = [['U1']]
    with pytest.raises(ValueError,match='answerability/evidence mismatch'):
        validate_dataset(dataset)


def test_reject_unrecorded_human_signoff(dataset):
    dataset['cases'][0]['annotation']['human_review_status'] = 'approved'
    with pytest.raises(ValueError,match='unsubstantiated review claim'):
        validate_dataset(dataset)


def test_reject_premature_split_label(dataset):
    dataset['cases'][0]['split'] = 'held_out'
    with pytest.raises(ValueError,match='step 2.2 split'):
        validate_dataset(dataset)
