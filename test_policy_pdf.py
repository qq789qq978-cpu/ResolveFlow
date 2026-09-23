"""Real PDF parsing and page-source contracts; no model or database required."""
import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from pypdf import PdfReader,PdfWriter
from pypdf.generic import DecodedStreamObject,NameObject
from policy_pdf import extract,read_document,validate_provenance
from rag import read_documents,rank,KNOWLEDGE
from policy_releases import prepare,context
from grounding import check_grounding

FIXTURE=Path(__file__).parent/'tests/fixtures/policies/materials.pdf'


def candidate(tmp_path):
    directory=tmp_path/'candidate';shutil.copytree(KNOWLEDGE,directory)
    for p in (FIXTURE,FIXTURE.with_suffix('.pdf.json')):shutil.copyfile(p,directory/p.name)
    docs,chunks=read_documents(directory)
    pdf=next(d for d in docs if d['source'].endswith('.pdf'))
    governance=json.loads((directory/'governance.json').read_text())
    governance['policies'][pdf['id']]={**governance['policies']['refund-v2'],'document_sha256':pdf['sha256']}
    (directory/'governance.json').write_text(json.dumps(governance))
    manifest=json.loads((directory/'release.json').read_text());manifest['id']='pdf-qa-v1'
    (directory/'release.json').write_text(json.dumps(manifest))
    return directory


def test_real_chinese_and_english_pages_are_stable_and_grounded(tmp_path):
    parsed=extract(FIXTURE)
    assert len(parsed['pages'])==2
    assert '订单编号与商品照片' in parsed['pages'][0]['text']
    assert '没有承诺退款到账时限' in parsed['pages'][1]['text']
    assert 'Synthetic QA document' in parsed['pages'][1]['text']
    payload=prepare(candidate(tmp_path));release=context(payload)
    hits=rank('订单编号 商品照片',payload['documents'],payload['chunks'],release=release,mode='demo')
    pdf=next(h for h in hits if h.get('source_type')=='pdf')
    assert pdf['page_start']==pdf['page_end']==1 and len(pdf['original_sha256'])==64
    proposal={'citation_schema':2,'citations':[pdf['chunk_id']], 'quotes':[{'chunk_id':pdf['chunk_id'],'quote':pdf['text']}],'evidence_status':'supported'}
    assert check_grounding(proposal,hits,documents=payload['documents'],release=release,mode='demo')['usable']
    pdf['page_start']=2
    assert 'source_provenance_mismatch' in check_grounding(proposal,hits,documents=payload['documents'],release=release,mode='demo')['errors']


@pytest.mark.parametrize('kind',['signature','damaged','encrypted','image_only','too_many_pages','oversize'])
def test_invalid_inputs_fail_with_actionable_error(tmp_path,kind):
    path=tmp_path/'bad.pdf'
    if kind=='signature':path.write_bytes(b'not a pdf')
    elif kind=='damaged':path.write_bytes(b'%PDF-1.4\ntruncated')
    elif kind=='oversize':path.write_bytes(b'%PDF-'+b'x'*(5*1024*1024))
    else:
        writer=PdfWriter()
        if kind=='encrypted':
            writer.append(FIXTURE);writer.encrypt('private-test-password')
        elif kind=='too_many_pages':
            for _ in range(65):writer.add_blank_page(width=100,height=100)
        else:
            page=writer.add_blank_page(width=100,height=100)
            stream=DecodedStreamObject();stream.set_data(b'0 0 100 100 re f')
            page[NameObject('/Contents')]=writer._add_object(stream)
        writer.write(path)
    with pytest.raises(ValueError,match='pdf_'):extract(path)


def test_pdf_parser_timeout_and_staging_does_not_claim_approval(monkeypatch,tmp_path):
    def hung(*args,**kwargs):raise subprocess.TimeoutExpired(args[0],15)
    monkeypatch.setattr(subprocess,'run',hung)
    with pytest.raises(ValueError,match='pdf_parse_timeout'):extract(FIXTURE)


def test_real_cli_stages_reviewable_pages_without_publishing(tmp_path):
    target=tmp_path/'draft'
    subprocess.run([sys.executable,'policy_pdf.py',str(FIXTURE),'--directory',str(target),
                    '--id','materials-pdf-v1','--title','PDF材料','--version','1'],check=True,capture_output=True)
    inspection=json.loads((target/'inspection.json').read_text(encoding='utf-8'))
    assert inspection['review_status']=='draft' and inspection['extracted_pages'][0]['page']==1
    assert '订单编号与商品照片' in inspection['extracted_pages'][0]['text']
    assert not (target/'governance.json').exists() and not (target/'release.json').exists()


def test_page_labels_do_not_replace_physical_pages(tmp_path):
    path=tmp_path/'labels.pdf';writer=PdfWriter();writer.append(FIXTURE)
    writer.set_page_label(0,0,style='/r');writer.set_page_label(1,1,style='/D')
    writer.write(path)
    pages=extract(path)['pages']
    assert [p['page'] for p in pages]==[1,2] and [p['label'] for p in pages]==['i','1']


def test_pdf_raw_bytes_or_page_map_tampering_invalidates_release(tmp_path):
    payload=prepare(candidate(tmp_path))
    d=next(d for d in payload['documents'] if d.get('provenance'))
    d['provenance']['file_sha256']='0'*64
    with pytest.raises(ValueError,match='identity'):validate_provenance(payload['documents'],payload['chunks'])


def test_missing_sidecar_and_duplicate_md_pdf_id_are_rejected(tmp_path):
    path=tmp_path/'policy.pdf';shutil.copyfile(FIXTURE,path)
    with pytest.raises(ValueError,match='sidecar'):read_documents(tmp_path)
    path.with_suffix('.pdf.json').write_text('{"id":"refund-v2","title":"Duplicate","version":"1"}')
    shutil.copyfile(KNOWLEDGE/'refund.md',tmp_path/'refund.md')
    with pytest.raises(ValueError,match='duplicate'):read_documents(tmp_path)


def test_pdf_does_not_autoapprove_or_override_action_rules(tmp_path):
    path=tmp_path/'policy.pdf';shutil.copyfile(FIXTURE,path)
    shutil.copyfile(FIXTURE.with_suffix('.pdf.json'),path.with_suffix('.pdf.json'))
    docs,chunks=read_documents(tmp_path)
    assert docs[0]['governance']=={} and rank('订单编号',docs,chunks,mode='demo')==[]
    directory=candidate(tmp_path);payload=prepare(directory)
    payload['action_chunks']['refund']=next(c['chunk_id'] for c in payload['chunks'] if c.get('page_start'))
    with pytest.raises(ValueError,match='incompatible'):context(payload)
