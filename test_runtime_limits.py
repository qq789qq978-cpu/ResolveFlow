import os
import subprocess
import sys
import time
from pathlib import Path
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from runtime_db import integer,runtime_dsn
from task_runtime import TaskRunner


@pytest.mark.parametrize('value',['0','-1','33','bad'])
def test_invalid_pool_limits(value,monkeypatch):
    monkeypatch.setenv('RF_DB_POOL_MAX',value)
    with pytest.raises(ValueError):integer('RF_DB_POOL_MAX',8,2,32)


def test_deadline_must_fit_queue_idle_limit(monkeypatch,tmp_path):
    monkeypatch.setenv('RF_TASK_TIMEOUT_SECONDS','240')
    with pytest.raises(ValueError,match='Idle transaction'):TaskRunner(tmp_path,'demo')


def test_dsn_preserves_schema_enforces_limits(monkeypatch):
    monkeypatch.setenv('RF_DB_LOCK_TIMEOUT_MS','500')
    d=conninfo_to_dict(runtime_dsn(make_conninfo(host='example',options='-c search_path=qa')))
    assert '-c search_path=qa' in d['options']
    assert 'lock_timeout=500' in d['options'] and 'statement_timeout=10000' in d['options']
    assert d['connect_timeout']=='5'


def test_mcp_keeps_short_deadline_and_attempt_identity(monkeypatch):
    monkeypatch.setenv('RF_DB_STATEMENT_TIMEOUT_MS','120000')
    monkeypatch.setenv('RF_DB_APPLICATION_NAME','rf-task-example')
    monkeypatch.setenv('RF_DB_MCP','1')
    d=conninfo_to_dict(runtime_dsn('host=example'))
    assert d['application_name']=='rf-task-example'
    assert 'statement_timeout=10000' in d['options'] and 'lock_timeout=10000' in d['options']


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Linux Docker parent-death contract')
def test_child_cannot_outlive_parent_even_in_separate_session(tmp_path):
    marker=tmp_path/'child.json'
    code='from task_runtime import parent_guard;parent_guard();import os,time;from pathlib import Path;Path('+repr(str(marker))+').write_text(str(os.getpid()));time.sleep(60)'
    parent_code='import os,subprocess,sys,time;p=subprocess.Popen([sys.executable,"-c",'+repr(code)+'],env={**os.environ,"RF_TASK_PARENT_PID":str(os.getpid())},start_new_session=True);time.sleep(60)'
    parent=subprocess.Popen([sys.executable,'-c',parent_code])
    try:
        deadline=time.monotonic()+10
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.05)
        assert marker.exists()
        pid=int(marker.read_text());parent.kill();parent.wait(timeout=3)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            stat=Path('/proc')/str(pid)/'stat'
            if not stat.exists() or stat.read_text().split(') ')[1].startswith('Z'):break
            time.sleep(.05)
        else:pytest.fail('MCP-style child survived its parent')
    finally:
        if parent.poll() is None:parent.kill();parent.wait()
