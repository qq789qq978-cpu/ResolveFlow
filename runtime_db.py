"""Bounded runtime connections; migrations retain their separate DDL contract."""
import os
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg_pool import ConnectionPool


def integer(name, default, low, high):
    value = int(os.getenv(name, str(default)))
    if not low <= value <= high:
        raise ValueError('Invalid runtime limit: '+name)
    return value


def runtime_dsn(dsn, *, application='resolveflow', mcp=False):
    mcp = mcp or os.getenv('RF_DB_MCP')=='1' or conninfo_to_dict(dsn).get('application_name','').startswith('resolveflow-mcp')
    statement = 10000 if mcp else integer('RF_DB_STATEMENT_TIMEOUT_MS',10000,100,120000)
    lock = 10000 if mcp else integer('RF_DB_LOCK_TIMEOUT_MS',5000,100,120000)
    idle = integer('RF_DB_IDLE_TRANSACTION_TIMEOUT_MS',240000,1000,900000)
    options = conninfo_to_dict(dsn).get('options','')
    application = os.getenv('RF_DB_APPLICATION_NAME',application)
    return make_conninfo(dsn, connect_timeout=5, application_name=application,
        options=options+f' -c statement_timeout={statement} -c lock_timeout={lock} -c idle_in_transaction_session_timeout={idle}')


def reset_connection(connection):
    # No request may leave SET/session state for the next pool borrower.
    connection.autocommit = True
    try: connection.execute('DISCARD ALL')
    finally: connection.autocommit = False


def pool(dsn, *, max_size=None):
    from psycopg.rows import dict_row
    return ConnectionPool(runtime_dsn(dsn), min_size=0,
        max_size=max_size or integer('RF_DB_POOL_MAX',8,2,32),
        timeout=integer('RF_DB_POOL_TIMEOUT_MS',2000,100,10000)/1000,
        max_waiting=integer('RF_DB_POOL_MAX_WAITING',16,1,128),
        max_idle=30, reconnect_timeout=5, open=True,
        kwargs={'row_factory':dict_row,'prepare_threshold':None},
        check=ConnectionPool.check_connection, reset=reset_connection,
        name='resolveflow-runtime')
