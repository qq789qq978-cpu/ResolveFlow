"""Local, bounded text-PDF ingestion with immutable physical-page provenance."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

PARSER='pypdf-6.19.0/plain-v1'
MAX_BYTES=5*1024*1024
MAX_PAGES=64
MAX_TEXT=200_000


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def extract(path):
    """Untrusted parsing lives in a time/memory bounded child, never in the API."""
    path=Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_BYTES:
        raise ValueError('pdf_file_invalid_or_over_5mb')
    try:
        result=subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'_extract',str(path.resolve())],
            capture_output=True,timeout=15,text=True,encoding='utf-8')
    except subprocess.TimeoutExpired:
        raise ValueError('pdf_parse_timeout') from None
    if result.returncode:
        raise ValueError('pdf_parser_failed')
    data=json.loads(result.stdout)
    if 'error' in data:raise ValueError(data['error'])
    return data


def _extract(path):
    if sys.platform.startswith('linux'):
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(10,10))
    from pypdf import PdfReader, __version__
    try:
        if __version__!='6.19.0':raise ValueError('pdf_parser_version_mismatch')
        if Path(path).stat().st_size>MAX_BYTES:raise ValueError('pdf_file_over_5mb')
        raw=Path(path).read_bytes()
        if not raw.startswith(b'%PDF-'):raise ValueError('pdf_invalid_signature')
        import io
        reader=PdfReader(io.BytesIO(raw),strict=True)
        if reader.is_encrypted:raise ValueError('pdf_encrypted')
        if not 1<=len(reader.pages)<=MAX_PAGES:raise ValueError('pdf_page_limit')
        pages=[];length=0
        for index,page in enumerate(reader.pages):
            text=page.extract_text().replace('\r\n','\n').replace('\r','\n').strip()
            length+=len(text)
            if length>MAX_TEXT:raise ValueError('pdf_text_limit')
            content=page.get_contents()
            if not text and content is not None and content.get_data().strip():
                raise ValueError('pdf_page_'+str(index+1)+'_has_no_extractable_text_requires_review_or_ocr')
            if '\ufffd' in text or any(ord(c)<32 and c not in '\n\t' for c in text):
                raise ValueError('pdf_page_'+str(index+1)+'_invalid_text_encoding')
            pages.append({'page':index+1,'label':str(reader.page_labels[index]),'text':text,
                          'has_images':bool(page.images)})
        if not any(p['text'] for p in pages):raise ValueError('pdf_has_no_text')
        return {'parser':PARSER,'file_sha256':hashlib.sha256(raw).hexdigest(),'pages':pages}
    except ValueError as error:
        if str(error).startswith('pdf_'):return {'error':str(error)}
        return {'error':'pdf_malformed'}
    except Exception:
        return {'error':'pdf_malformed'}


def identity(document):
    return digest({k:document[k] for k in ('id','title','version','source','body','provenance')})


def read_document(path,directory):
    path=Path(path);directory=Path(directory).resolve()
    sidecar=path.with_suffix(path.suffix+'.json')
    if path.is_symlink() or not path.resolve().is_relative_to(directory):raise ValueError('PDF must remain inside source directory')
    if not sidecar.is_file():raise ValueError('PDF policy import requires a .pdf.json metadata sidecar')
    if sidecar.is_symlink() or not sidecar.resolve().is_relative_to(directory) or sidecar.stat().st_size>10_000:
        raise ValueError('Invalid PDF metadata sidecar')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('Duplicate PDF metadata key')
            result[k]=v
        return result
    meta=json.loads(sidecar.read_text(encoding='utf-8-sig'),object_pairs_hook=unique)
    if set(meta)!={'id','title','version'} or any(not isinstance(v,str) or not v.strip() or len(v)>200 for v in meta.values()):
        raise ValueError('PDF metadata requires id, title and version strings')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}',meta['id']):raise ValueError('Invalid PDF document id')
    parsed=extract(path)
    document={**meta,'source':path.resolve().relative_to(directory).as_posix(),
              'body':'\n\f\n'.join(p['text'] for p in parsed['pages']),
              'provenance':{'kind':'pdf',**parsed},'governance':{}}
    document['sha256']=identity(document)
    chunks=[];line=1
    for page in parsed['pages']:
        text=page['text']
        # Chunk independently per physical page; lines always refer to extracted
        # text, never pretend these are Markdown source lines or printed labels.
        offset=0
        for paragraph in re.split(r'\n\s*\n',text):
            start_offset=text.find(paragraph,offset);offset=start_offset+len(paragraph)
            for start in range(0,len(paragraph),440):
                piece=paragraph[start:start+500]
                if not piece.strip():continue
                position=len(chunks)
                chunks.append({'chunk_id':f'{meta["id"]}:{document["sha256"][:12]}:{position}',
                    'document_id':meta['id'],'position':position,'text':piece,
                    'line_start':line+text[:start_offset+start].count('\n'),
                    'line_end':line+text[:start_offset+start+len(piece)].count('\n'),
                    'page_start':page['page'],'page_end':page['page'],'page_label':page['label']})
                if start+500>=len(paragraph):break
        line+=len(text.splitlines())+1
    return document,chunks


def validate_provenance(documents,chunks):
    for document in documents:
        provenance=document.get('provenance')
        if not provenance:
            if document['source'].lower().endswith('.pdf'):raise ValueError('PDF provenance missing')
            continue
        if (provenance.get('kind')!='pdf' or provenance.get('parser')!=PARSER
                or not re.fullmatch('[0-9a-f]{64}',provenance.get('file_sha256',''))
                or identity(document)!=document['sha256']):raise ValueError('PDF provenance identity mismatch')
        pages=provenance['pages']
        if not 1<=len(pages)<=MAX_PAGES or [p['page'] for p in pages]!=list(range(1,len(pages)+1)):
            raise ValueError('Invalid PDF physical pages')
        if document['body']!='\n\f\n'.join(p['text'] for p in pages):raise ValueError('PDF extracted body mismatch')
        for chunk in (c for c in chunks if c['document_id']==document['id']):
            p=chunk.get('page_start')
            if type(p) is not int or not 1<=p<=len(pages) or chunk.get('page_end')!=p:
                raise ValueError('PDF chunk page missing or invalid')
            if chunk['text'] not in pages[p-1]['text'] or chunk.get('page_label')!=pages[p-1]['label']:
                raise ValueError('PDF chunk does not match physical page')


def evidence_location(document,chunk):
    if not document.get('provenance'):return {}
    return {'source_type':'pdf','page_start':chunk['page_start'],'page_end':chunk['page_end'],
            'page_label':chunk['page_label'],'original_sha256':document['provenance']['file_sha256'],
            'parser':document['provenance']['parser']}


def enrich_index(documents,chunks,payload):
    """The release archive stores provenance without a destructive schema upgrade."""
    docs={d['id']:d for d in payload['documents']}
    archived={c['chunk_id']:c for c in payload['chunks']}
    for d in documents:
        if docs[d['id']].get('provenance'):d['provenance']=docs[d['id']]['provenance']
    for c in chunks:
        c.update({k:v for k,v in archived[c['chunk_id']].items() if k.startswith('page_')})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--directory',type=Path,required=True,help='New staging directory; never auto-publishes')
    for key in ('id','title','version'):parser.add_argument('--'+key,required=True)
    args=parser.parse_args()
    extract(args.source)  # fail before creating any staging output
    if args.directory.exists():parser.error('Use a new staging directory')
    args.directory.mkdir(parents=True)
    path=args.directory/(args.id+'.pdf')
    try:
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}',args.id):raise ValueError('Invalid document id')
        shutil.copyfile(args.source,path)
        path.with_suffix('.pdf.json').write_text(json.dumps({k:getattr(args,k) for k in ('id','title','version')},ensure_ascii=False,indent=2),encoding='utf-8')
        doc,chunks=read_document(path,args.directory)
        (args.directory/'inspection.json').write_text(json.dumps({'document_sha256':doc['sha256'],
            'original_sha256':doc['provenance']['file_sha256'],'pages':len(doc['provenance']['pages']),
            'chunks':len(chunks),'review_status':'draft','parser':PARSER,
            'extracted_pages':doc['provenance']['pages'],
            'images_not_extracted':any(p['has_images'] for p in doc['provenance']['pages'])},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'staged':True,'document_sha256':doc['sha256'],'review_status':'draft'}))
    except Exception:
        # Keep failed staging for diagnosis; never touched an active release.
        raise

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='_extract':print(json.dumps(_extract(sys.argv[2]),ensure_ascii=False))
    else:main()
