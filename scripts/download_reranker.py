"""Download fixed public model assets only; never sends queries or credentials."""
import hashlib
import argparse
import json
from pathlib import Path
import urllib.request

MODEL='cross-encoder/mmarco-mMiniLMv2-L12-H384-v1'
REVISION='1427fd652930e4ba29e8149678df786c240d8825'
FILES=('config.json','model.safetensors','tokenizer.json','tokenizer_config.json',
       'special_tokens_map.json','sentencepiece.bpe.model','README.md')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    output=args.directory;output.mkdir(parents=True,exist_ok=True)
    hashes={}
    for name in FILES:
        path=output/name
        if not path.exists():
            temporary=path.with_suffix(path.suffix+'.part')
            with urllib.request.urlopen(f'https://huggingface.co/{MODEL}/resolve/{REVISION}/{name}',timeout=60) as response, temporary.open('wb') as stream:
                while block:=response.read(1024*1024):stream.write(block)
            temporary.replace(path)
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
        print(json.dumps({'file':name,'bytes':path.stat().st_size}),flush=True)
    (output/'manifest.json').write_text(json.dumps({'model':MODEL,'revision':REVISION,'license':'apache-2.0','sha256':hashes},indent=2)+'\n')

if __name__=='__main__':main()
