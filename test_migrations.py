"""Offline revision contract; no database or model required."""
from io import StringIO
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
import pytest

ROOT = Path(__file__).resolve().parent


def config(output=None):
    return Config(str(ROOT/'alembic.ini'), output_buffer=output)


def test_independent_optional_branch():
    script = ScriptDirectory.from_config(config())
    assert set(script.get_heads()) == {'rf_core_0001','rf_vector_0001'}
    core, vector = (script.get_revision(n) for n in ('core@head','vector@head'))
    assert core.down_revision is None and core.dependencies is None
    assert vector.down_revision is None and vector.dependencies == core.revision


@pytest.mark.parametrize('target,vector', [('core@head',False),('vector@head',True)])
def test_offline_ownership_and_no_demo_seed(monkeypatch, target, vector):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('RF_MIGRATION_SCHEMA', raising=False)
    out = StringIO()
    command.upgrade(config(out), target, sql=True)
    rendered = out.getvalue()
    assert 'CREATE TABLE rf_orders' in rendered
    assert ('CREATE TABLE rf_policy_vectors' in rendered) == vector
    assert 'rf_schema_version' in rendered
    for forbidden in ('checkpoints','checkpoint_blobs','checkpoint_writes','checkpoint_migrations',
                      'CREATE EXTENSION','DROP EXTENSION','RF-1001','IF NOT EXISTS'):
        assert forbidden not in rendered
    # Only singleton control state and Alembic bookkeeping may be initialized.
    for line in rendered.splitlines():
        if line.startswith('INSERT INTO'):
            assert line.startswith(('INSERT INTO rf_policy_head','INSERT INTO public.rf_schema_version'))


@pytest.mark.parametrize('schema',['public; DROP SCHEMA public','pg_catalog','information_schema'])
def test_unsafe_schema_rejected(monkeypatch, schema):
    monkeypatch.setenv('RF_MIGRATION_SCHEMA', schema)
    with pytest.raises(ValueError, match='Invalid'):
        command.upgrade(config(StringIO()), 'core@head', sql=True)
