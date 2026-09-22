"""Release contracts and deterministic version pinning, without database/model IO."""
import copy
import hashlib
import json
import shutil

import pytest

from grounding import check_grounding, demo_suggestion
from policy_releases import context, prepare, validate_payload
from rag import KNOWLEDGE, rank, read_documents


def bundle(tmp_path, *, release_id='qa-policy-b', version='3', change_clause=False):
    directory=tmp_path/release_id
    shutil.copytree(KNOWLEDGE,directory)
    p=directory/'refund.md'
    text=p.read_text(encoding='utf-8').replace('id: refund-v2','id: refund-v'+version).replace("version: '2'", "version: '"+version+"'")
    if change_clause: text=text.replace('7天','14天')
    p.write_text(text,encoding='utf-8')
    meta=json.loads((directory/'governance.json').read_text())
    review=meta['policies'].pop('refund-v2')
    review['document_sha256']=hashlib.sha256(text.encode()).hexdigest()
    meta['policies']['refund-v'+version]=review
    (directory/'governance.json').write_text(json.dumps(meta),encoding='utf-8')
    docs,chunks=read_documents(directory)
    manifest=json.loads((directory/'release.json').read_text())
    manifest['id']=release_id
    manifest['action_chunks']['refund']=next(c['chunk_id'] for c in chunks if c['document_id']=='refund-v'+version and c['position']==0)
    (directory/'release.json').write_text(json.dumps(manifest),encoding='utf-8')
    return directory


def test_new_policy_document_version_can_explicitly_bind_same_unchanged_rule(tmp_path):
    payload=prepare(bundle(tmp_path))
    ctx=context(payload,2)
    hits=rank('申请退款',payload['documents'],payload['chunks'],release=ctx,mode='demo')
    proposal=demo_suggestion('申请退款',hits)
    assert proposal['citations'][0].startswith('refund-v3:')
    result=check_grounding(proposal,hits,documents=payload['documents'],release=ctx,mode='demo')
    assert result['usable'] and result['action_chunks']['refund']==proposal['citations'][0]
    assert ctx['token']['refund_rule']['version']=='refund-v2'


@pytest.mark.parametrize('field', ['version','sha256'])
def test_manifest_cannot_select_unknown_rule_code(tmp_path,field):
    directory=bundle(tmp_path)
    path=directory/'release.json'
    manifest=json.loads(path.read_text())
    manifest['refund_rule'][field]='unknown'
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='executable refund rule'): prepare(directory)


def test_changed_refund_qualification_needs_compatible_code_even_with_updated_hashes(tmp_path):
    with pytest.raises(ValueError,match='incompatible with executable rules'):
        prepare(bundle(tmp_path,change_clause=True))


def test_manifest_cannot_bless_changed_code_without_an_explicit_code_clause_binding(monkeypatch):
    payload=prepare()
    changed={'version':'refund-v3','sha256':'0'*64}
    monkeypatch.setattr('policy_releases.rule_contract',lambda:changed)
    payload['refund_rule']=changed
    with pytest.raises(ValueError,match='executable refund rule'):validate_payload(payload)


@pytest.mark.parametrize('mutation', ['missing','duplicate','wrong_binding'])
def test_incomplete_or_ambiguous_release_manifest_is_rejected(tmp_path,mutation):
    directory=bundle(tmp_path)
    p=directory/'release.json'
    data=json.loads(p.read_text())
    if mutation=='missing': data.pop('action_chunks')
    if mutation=='wrong_binding': data['action_chunks']['refund']=data['action_chunks']['reply']
    p.write_text(json.dumps(data))
    if mutation=='duplicate': p.write_text('{"schema":1,"schema":1}')
    with pytest.raises(ValueError): prepare(directory)


@pytest.mark.parametrize('mutation',['missing','other_release','same_release_new_generation'])
def test_saved_evidence_cannot_cross_activation_generations(mutation):
    payload=prepare()
    ctx=context(payload,1)
    hits=rank('申请退款',payload['documents'],payload['chunks'],release=ctx)
    proposal=demo_suggestion('申请退款',hits)
    if mutation=='missing':
        for hit in hits: hit.pop('release')
    if mutation=='other_release':
        payload=copy.deepcopy(payload);payload['id']='other-release'
    if mutation=='same_release_new_generation': ctx=context(payload,2)
    else: ctx=context(payload,1)
    result=check_grounding(proposal,hits,documents=payload['documents'],release=ctx)
    assert result['reference_valid'] and not result['usable']
    assert 'release_changed_or_missing' in result['errors']
