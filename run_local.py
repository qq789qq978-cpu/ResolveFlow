"""Supervise API and worker in one terminal; exits both on Ctrl+C or child failure."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parent

def command(module, args):
    # On Windows use the base executable directly so terminate() targets the real
    # child, while processing the venv's .pth files (including pywin32).
    packages=str(Path(sys.prefix)/'Lib/site-packages') if os.name=='nt' else None
    if packages:
        bootstrap=f'import site,sys,runpy;site.addsitedir({packages!r});sys.path.insert(0,{str(ROOT)!r});sys.argv={[module,*args]!r};runpy.run_module({module!r},run_name="__main__")'
        return [sys._base_executable,'-c',bootstrap]
    return [sys.executable,'-m',module,*args]

def main():
    from dotenv import load_dotenv
    from database_state import require_ready
    load_dotenv(ROOT/'.env', override=False, encoding='utf-8-sig')
    from db_roles import require_app
    require_app(os.environ['DATABASE_URL'])
    if not os.getenv('READONLY_DATABASE_URL'):
        raise ValueError('Configure READONLY_DATABASE_URL before starting local services; see DATABASE_ROLES.md')
    os.environ['RF_ENFORCE_DB_ROLES']='1'
    require_ready(os.environ['DATABASE_URL'])
    children=[]
    try:
        children.append(subprocess.Popen(command('uvicorn',['operations:app','--host','127.0.0.1','--port','8003','--workers','1']),cwd=ROOT))
        children.append(subprocess.Popen(command('worker',[]),cwd=ROOT))
        print('ResolveFlow: http://127.0.0.1:8003/ (API + worker). Ctrl+C stops both.',flush=True)
        while all(p.poll() is None for p in children):time.sleep(1)
        raise RuntimeError('A service exited. Read the messages above before restarting.')
    except KeyboardInterrupt:
        pass
    finally:
        for p in children:
            if p.poll() is None:p.terminate()
        for p in children:
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:p.kill();p.wait()

if __name__=='__main__':main()
