"""A database sees one interface per Worker, even when it has two networks."""
import importlib
from pathlib import Path
import pytest


@pytest.mark.parametrize('clients,expected',[
    (['172.29.0.4','172.29.0.5'],True),
    (['172.29.0.4','172.30.0.3'],False),  # two interfaces of one Worker
    (['172.29.0.4','192.0.2.1'],False),  # unknown client
    (['172.29.0.4'],False),             # only one backend
    (['172.29.0.4',''],False),          # missing address
])
def test_worker_identity_with_multiple_networks(monkeypatch,clients,expected):
    monkeypatch.syspath_prepend(str(Path(__file__).parent/'scripts'))
    check=importlib.import_module('multi_worker_qa').distinct_worker_clients
    workers=[{'id':identity,'networks':{
        'default':{'IPAddress':primary},'embedding-private':{'IPAddress':secondary}}}
        for identity,primary,secondary in [('one','172.29.0.4','172.30.0.3'),('two','172.29.0.5','172.30.0.4')]]
    rows=[{'client_addr':address} for address in clients]
    assert check(rows,workers) is expected


@pytest.mark.parametrize('existing_kind', ['container', 'volume', 'network'])
def test_fault_qa_refuses_existing_resources_without_starting(monkeypatch, tmp_path, existing_kind):
    monkeypatch.syspath_prepend(str(Path(__file__).parent/'scripts'))
    qa = importlib.import_module('worker_crash_qa')
    monkeypatch.setattr(qa, 'PROJECT', 'resolveflow-qa-preserved')
    monkeypatch.setattr(qa, 'ROOT', tmp_path)
    calls = []
    def docker(*args):
        calls.append(args)
        assert args[1] == 'ls'  # No mutation, including when only stopped containers exist.
        if args[0] == 'container':
            assert '-a' in args
        return 'existing-resource' if args[0] == existing_kind else ''
    monkeypatch.setattr(qa, 'docker', docker)
    with pytest.raises(ValueError, match='fresh project'):
        qa.fault_compose(tmp_path/'empty.env')
    assert calls and not (tmp_path/'work').exists()
