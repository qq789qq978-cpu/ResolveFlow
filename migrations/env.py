"""Application migrations only; LangGraph owns its checkpoint migrations."""
import os
import re

import psycopg
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

schema = os.environ.get('RF_MIGRATION_SCHEMA', 'public')
if not re.fullmatch(r'[a-z][a-z0-9_]{0,62}', schema) or schema.startswith('pg_') or schema == 'information_schema':
    raise ValueError('Invalid application migration schema')

options = dict(target_metadata=None, version_table='rf_schema_version',
               version_table_schema=schema, transactional_ddl=True)

if context.is_offline_mode():
    context.configure(url='postgresql+psycopg://', literal_binds=True, **options)
    with context.begin_transaction():
        context.execute(f'SET LOCAL search_path TO "{schema}"')
        context.run_migrations()
else:
    dsn = os.environ.get('DATABASE_URL')
    if not dsn:
        raise ValueError('DATABASE_URL is required')
    engine = create_engine('postgresql+psycopg://', poolclass=NullPool,
                           creator=lambda: psycopg.connect(dsn, connect_timeout=5),
                           hide_parameters=True)
    try:
        with engine.connect() as connection:
            # Own one transaction, including search_path and the version table.
            with connection.begin():
                connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
                if connection.exec_driver_sql('SELECT current_schema()').scalar() != schema:
                    raise ValueError('Migration schema must already exist')
                context.configure(connection=connection, **options)
                with context.begin_transaction():
                    context.run_migrations()
    finally:
        engine.dispose()
