"""Download public, revision-pinned model files; never reads .env or business data."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def verify(path, item):
    if not path.is_file() or path.is_symlink() or path.stat().st_size!=item['bytes']:return False
    digest=hashlib.sha256() if item['sha256'] else hashlib.sha1()
    if not item['sha256']:digest.update(('blob '+str(item['bytes'])+'\0').encode())
    with path.open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()==(item['sha256'] or item['git_blob_sha1'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    if args.report.exists():parser.error('Use a new report path')
    manifest=json.loads((ROOT/'embedding/model-manifest.json').read_text(encoding='utf-8'))
    args.directory.mkdir(parents=True,exist_ok=True)
    rows=[]
    for item in manifest['files']:
        target=args.directory/item['name']
        if not verify(target,item):
            partial=target.with_suffix(target.suffix+'.partial')
            url=f"https://huggingface.co/{manifest['model']}/resolve/{manifest['revision']}/{item['name']}"
            with urllib.request.urlopen(url,timeout=60) as response, partial.open('wb') as output:
                for chunk in iter(lambda:response.read(1024*1024),b''):output.write(chunk)
            if not verify(partial,item):raise ValueError('Model file checksum mismatch: '+item['name'])
            partial.replace(target)
        rows.append({'name':item['name'],'bytes':item['bytes'],'verified':True})
        print('Verified '+item['name'],flush=True)
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps({'model':manifest['model'],'revision':manifest['revision'],
         'files':rows,'paid_calls':0,'business_data_sent':False},indent=2)+'\n',encoding='utf-8')


if __name__=='__main__':main()
