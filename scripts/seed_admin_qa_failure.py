"""Create a retry-exhausted fixture through the queue handler, in demo QA only.

Stop the isolated QA worker first. Feed this file to Python inside its API
container with the UUID of a NEW browser-created RF-1002 ticket as argv[1].
No application files are patched, and no run status is edited directly.
"""
import json
import os
import sys
import uuid

import jobs
from storage import Store


def main():
    if os.getenv('MODE') != 'demo' or os.getenv('APP_API_KEY') != 'qa-step11-operator':
        raise SystemExit('This fixture is restricted to the isolated demo QA configuration')
    run_id = str(uuid.UUID(sys.argv[1]))
    store = Store(os.environ['DATABASE_URL'])
    row = store.get(run_id)
    job = jobs.details(store, run_id)['job']
    if not (row['mode'] == 'demo' and row['status'] == 'queued'
            and row['order_id'] == 'RF-1002' and row['created_by'] == 'admin'
            and row['ticket'].startswith('步骤1.3')
            and job['kind'] == 'investigate' and job['attempts'] == 0):
        raise SystemExit('Expected a new, untouched step 1.3 admin QA ticket')

    class BrokenInvestigator:
        mode = 'demo'

        def recover(self, *args):
            raise TimeoutError('qa-step13-private-error-marker')

    observations = []
    for expected in ('retrying', 'retrying', 'failed'):
        assert jobs.process_one(store, BrokenInvestigator(), run_id, retry_delay=0)
        row = store.get(run_id)
        job = jobs.details(store, run_id)['job']
        assert row['status'] == expected
        assert 'qa-step13-private-error-marker' not in str(row) + str(job)
        observations.append({'run_status': row['status'], 'job_status': job['status'],
                             'attempts': job['attempts'], 'error': row['error'],
                             'last_error': job['last_error']})
    assert not jobs.process_one(store, BrokenInvestigator(), run_id, retry_delay=0)
    print(json.dumps({'run_id': run_id, 'fixture': 'injected investigator TimeoutError',
                      'observations': observations}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
