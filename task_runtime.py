"""Isolate graph execution so a deadline stops work before releasing its job lock."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid
from contextlib import ExitStack, contextmanager
import psycopg
from runtime_db import integer
from observability import event


class TaskDeadlineExceeded(TimeoutError):pass
class TaskExecutionFailed(RuntimeError):pass


def parent_guard():
    """Linux parent-death signal also covers the separately-sessioned MCP child."""
    expected = os.getenv('RF_TASK_PARENT_PID')
    if expected and sys.platform.startswith('linux'):
        import ctypes
        if ctypes.CDLL(None,use_errno=True).prctl(1,signal.SIGKILL,0,0,0) != 0:
            raise RuntimeError('Cannot install task parent guard')
        if os.getppid()!=int(expected):os._exit(1)


def stop_child(process):
    if process.poll() is None:
        if sys.platform=='win32':
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                           capture_output=True,timeout=5)
        else:process.kill()
    process.wait(timeout=5)


@contextmanager
def cleanup_channels():
    """Reserve cancellation capacity BEFORE starting work, even if PG fills up."""
    from psycopg.conninfo import make_conninfo
    with ExitStack() as stack:
        channels=[]
        for dsn in dict.fromkeys(filter(None,(os.getenv('DATABASE_URL'),os.getenv('READONLY_DATABASE_URL')))):
            channels.append(stack.enter_context(psycopg.connect(make_conninfo(dsn,
                connect_timeout=2, application_name='resolveflow-task-cleanup',
                options='-c statement_timeout=2500 -c lock_timeout=1000'),autocommit=True)))
        yield channels


def cleanup_connections(token, channels):
    """Each non-superuser terminates only its own connections for this attempt."""
    for c in channels:
        c.execute('SELECT pg_terminate_backend(pid,1000) FROM pg_stat_activity '
            'WHERE application_name=%s AND usename=current_user AND pid<>pg_backend_pid()', (token,)).fetchall()
        remaining=c.execute('SELECT count(*) FROM pg_stat_activity '
            'WHERE application_name=%s AND usename=current_user', (token,)).fetchone()[0]
        if remaining:
            raise psycopg.OperationalError('Task connection cleanup incomplete')


class TaskRunner:
    def __init__(self,directory,mode):
        self.directory=str(directory);self.mode=mode;self.process=None
        self.timeout=integer('RF_TASK_TIMEOUT_SECONDS',180,1,600)
        idle=integer('RF_DB_IDLE_TRANSACTION_TIMEOUT_MS',240000,1000,900000)
        if idle < (self.timeout+30)*1000:
            raise ValueError('Idle transaction limit must exceed task deadline by 30 seconds')

    def recover(self,run_id,ticket,order_id,approval=None):
        with cleanup_channels() as channels:
            return self._recover(channels,run_id,ticket,order_id,approval)

    def _recover(self,channels,run_id,ticket,order_id,approval):
        # A separate interpreter owns every graph/checkpoint connection. Nothing
        # continues in a background Python thread after this method times out.
        token='rf-task-'+uuid.uuid4().hex
        env={**os.environ,'RF_TASK_PARENT_PID':str(os.getpid()),'RF_DB_APPLICATION_NAME':token}
        package=str(Path(psycopg.__file__).resolve().parent.parent)
        script=str(Path(__file__).with_name('worker_task.py'))
        bootstrap='import site,runpy,sys;site.addsitedir(sys.argv[1]);sys.path.insert(0,sys.argv[2]);runpy.run_path(sys.argv[3],run_name="__main__")'
        process=self.process=subprocess.Popen([getattr(sys,'_base_executable',sys.executable),
            '-c',bootstrap,package,str(Path(__file__).parent),script],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            env=env,text=True,encoding='utf-8')
        event('task_started',task_token=token)
        try:
            payload={'directory':self.directory,'mode':self.mode,'run_id':run_id,
                'ticket':ticket,'order_id':order_id,'approval':approval}
            try:out,_=process.communicate(json.dumps(payload,default=str),timeout=self.timeout)
            except subprocess.TimeoutExpired:
                event('task_deadline',task_token=token)
                stop_child(process)
                raise TaskDeadlineExceeded('Task execution deadline reached') from None
            if process.returncode:raise TaskExecutionFailed('Task process stopped')
            message=json.loads(out)
            if message.get('infrastructure'):raise psycopg.OperationalError('Task database unavailable')
            if message.get('error'):
                kind=message['error']
                if kind=='QueryCanceled':raise psycopg.errors.QueryCanceled('Task SQL deadline')
                if kind=='LockNotAvailable':raise psycopg.errors.LockNotAvailable('Task lock deadline')
                raise TaskExecutionFailed('Task execution failed')
            return message['result']
        finally:
            if process.poll() is None:stop_child(process)
            for stream in (process.stdin,process.stdout):
                if stream:stream.close()
            self.process=None
            # Do this for crashes and successful exits too, before the caller
            # can commit/release its queue lock. Failures remain infrastructure
            # errors; never claim successful cancellation without evidence.
            cleanup_connections(token,channels)
            event('task_cleaned',task_token=token)

    def close(self):
        if self.process:stop_child(self.process)
