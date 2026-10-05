"""Timed recovery and endpoint rollback using NEW trial-only restored volumes."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.local_trial import API, load, read, write, now, stamp, sql, lock, healthy, status
from scripts.local_backup import Stack, restore, verify

def wait_for(fn,seconds=110):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if fn():return True
        time.sleep(1)
    return False

def events(s):
    result=[]
    for line in s.cmd('logs','--no-log-prefix','alpha-monitor').decode().splitlines():
        try:result.append(json.loads(line))
        except ValueError:pass
    return result

def drill(work,final=False):
    work,state,source=load(work)
    with lock(work):
        if final:
            gate=status(work,state)
            if not gate['calendar_requirement_met'] or not gate['backup_requirement_met']:
                raise ValueError('Real seven-day evidence required before final drill')
        backup=state['backups'][-1];mirror=Path(backup['copy']);manifest=verify(mirror)
        prefix=state['project']+'-'+now().strftime('%d%H%M%S')
        if len(prefix.removeprefix('resolveflow-accounts-'))>28:raise ValueError('Drill project prefix too long')
        out=Path(state['evidence'])/'drills'/f'{stamp()}.json'
        report={'passed':False,'started_at':now().isoformat(),'backup_id':backup['id'],'final':final,
                'checks':[],'projects':[],'model_api_calls':0,'real_refunds':0,
                'scope':'isolated endpoint switch and snapshot rollback, same frozen image; no schema downgrade'}
        targets=[];probe=None;handle=None
        def check(name,condition):
            report['checks'].append({'check':name,'passed':bool(condition)})
            write(out,report);print(json.dumps(report['checks'][-1]),flush=True)
            if not condition:raise AssertionError(name)
        def validate_target(s,port):
            api=API(port);pw=read(work/'trial-accounts.json')['password']
            a=api.login('admina',pw);review=api.login('reviewera',pw);b=api.login('adminb',pw)
            pending=state['pending_run']
            check('original pending approval and checkpoint readable',api.call('/api/runs/'+pending,a)['status']=='awaiting_approval')
            api.call('/api/runs/'+pending,b,status=404)
            api.call('/api/runs/'+pending+'/approval',review,{'approved':True,'reason':'isolated recovery drill'},202)
            check('original pending approval resumes to simulated refund',api.wait(pending,a)['status']=='refunded')
            api.call('/api/runs/'+pending+'/approval',review,{'approved':True,'reason':'duplicate'},409)
            before=sql(s,'alpha','SELECT * FROM rf_refunds ORDER BY order_id')
            r=api.call('/api/runs',a,{'order_id':'RF-1004','ticket':'申请退款'},202)
            check('new request for approval-required order still requires fresh approval',api.wait(r['id'],a)['status']=='awaiting_approval')
            api.call('/api/runs/'+r['id']+'/approval',review,{'approved':True,'reason':'duplicate request still requires approval'},202)
            check('duplicate new request preserves original refund ledger',api.wait(r['id'],a)['status']=='already_refunded' and before==sql(s,'alpha','SELECT * FROM rf_refunds ORDER BY order_id'))
            check('restored three-worker allocation and identity quota retained',
                  s.config['services']['alpha-monitor']['environment']['RF_EXPECTED_WORKERS']=='2'
                  and manifest['capacity'] and manifest['identity']['capacity_admissions']['count']>0)
            check('recovered endpoint and twelve services healthy',healthy(s))
            return api,a
        try:
            # A post-backup run is an explicit synthetic loss-window sentinel.
            api=API(source.meta['port']);pw=read(work/'trial-accounts.json')['password'];token=api.login('admina',pw)
            sentinel=api.call('/api/runs',token,{'order_id':'RF-1002','ticket':'申请退款'},202)
            check('post-backup sentinel completes on untouched source',api.wait(sentinel['id'],token)['status']=='auto_rejected')
            incident=now();begin=time.monotonic()
            report['incident_declared_at']=incident.isoformat()
            report['rpo_seconds']=(incident-datetime.fromisoformat(manifest['created_at'])).total_seconds()
            check('latest independent backup age within 24h RPO',0<=report['rpo_seconds']<=86400)
            restored,verification=restore(mirror,prefix+'a',8058);candidate=Stack(restored);targets.append(candidate)
            report['projects'].append(candidate.project);report['restore']=verification
            API(8058).call('/api/me',token,status=401)
            check('source session is revoked on independent restore',True)
            current,admin=validate_target(candidate,8058)
            current.call('/api/runs/'+sentinel['id'],admin,status=404)
            check('post-backup sentinel absent only on restored snapshot and source remains readable',api.call('/api/runs/'+sentinel['id'],token)['status']=='auto_rejected')
            report['selected_endpoint']='http://127.0.0.1:8058'
            report['recovery_rto_seconds']=time.monotonic()-begin
            check('restore and business verification within four-hour RTO',report['recovery_rto_seconds']<=14400)
            # Real host observer, deliberately fault only the restored candidate.
            log=restored/'drill-probe.jsonl';handle=log.open('w',encoding='utf-8')
            kwargs={'stdout':handle,'stderr':subprocess.DEVNULL}
            if sys.platform=='win32':kwargs['creationflags']=subprocess.CREATE_NO_WINDOW
            probe=subprocess.Popen([sys.executable,str(ROOT/'scripts/local_probe.py'),'--url',current.base+'/health','--interval','1'],**kwargs)
            check('independent host probe initially sees candidate',wait_for(lambda:log.exists() and 'local_entry_recovered' in log.read_text()))
            start=time.monotonic();candidate.cmd('stop','gateway')
            check('injected candidate outage observed within 120 seconds',wait_for(lambda:'local_entry_unavailable' in log.read_text(),100))
            report['entry_detection_seconds']=time.monotonic()-start
            assert report['entry_detection_seconds']<=120
            candidate.cmd('start','gateway')
            check('candidate gateway recovery observed',wait_for(lambda:log.read_text().count('local_entry_recovered')>=2))
            # This is the meaningful partial-worker regression: one of two fails.
            check('both alpha workers initially online',wait_for(lambda:current.call('/api/alerts',admin)['workers_online']>=2))
            offset=len(events(candidate));start=time.monotonic();candidate.cmd('stop','alpha-extra-worker')
            check('single alpha worker loss raises local offline alert within 120 seconds',wait_for(lambda:any(e.get('code')=='worker_offline' and e.get('transition')=='firing' for e in events(candidate)[offset:]),100))
            report['partial_worker_detection_seconds']=time.monotonic()-start
            assert report['partial_worker_detection_seconds']<=120
            candidate.cmd('stop','alpha-worker')
            queued=current.call('/api/runs',admin,{'order_id':'RF-1002','ticket':'申请退款'},202)['id']
            sql(candidate,'alpha',"UPDATE rf_jobs SET available_at=now()-interval '2 minutes' WHERE run_id=%s",(queued,))
            start=time.monotonic()
            check('injected old queue entry raises local delay alert',wait_for(lambda:any(e.get('code')=='queue_delayed' and e.get('run_id')==queued and e.get('transition')=='firing' for e in events(candidate)[offset:]),100))
            report['queue_detection_seconds']=time.monotonic()-start
            assert report['queue_detection_seconds']<=120
            # Explicit synthetic terminal-failure fixture, not fabricated real executions.
            sql(candidate,'alpha',"UPDATE rf_jobs SET status='failed',attempts=3,last_error='TrialInjectedFailure' WHERE run_id=%s",(queued,))
            sql(candidate,'alpha',"UPDATE rf_runs SET status='failed' WHERE id=%s",(queued,))
            for _ in range(3):sql(candidate,'alpha',"INSERT INTO rf_job_attempts(run_id,kind,success,error_type,elapsed_ms) VALUES (%s,'investigate',false,'TrialInjectedFailure',1)",(queued,))
            start=time.monotonic()
            check('injected failed and consecutive-failure fixtures raise local alerts',wait_for(lambda:{'job_failed','consecutive_failures'}<={e.get('code') for e in events(candidate)[offset:] if e.get('transition')=='firing'},100))
            report['failure_fixture_detection_seconds']=time.monotonic()-start
            assert report['failure_fixture_detection_seconds']<=120
            report['failure_injection']='three synthetic attempt records and failed state; not three actual worker failures'
            current.call('/api/runs/'+queued+'/retry',admin,{},202)
            candidate.cmd('start','alpha-worker','alpha-extra-worker')
            check('original queued run recovers through admin retry',current.wait(queued,admin)['status']=='auto_rejected')
            check('all injected alert incidents resolve',wait_for(lambda:not current.call('/api/alerts',admin)['alerts']))
            report['monitor_events']=events(candidate)[offset:]
            probe.terminate();probe.wait(timeout=10);probe=None;handle.close();handle=None
            report['entry_events']=[json.loads(line) for line in log.read_text().splitlines()]
            # Declare rollback, abandon candidate writes, restore original frozen snapshot.
            start=time.monotonic();report['rollback_declared_at']=now().isoformat();candidate.cmd('stop')
            rolled,verification=restore(mirror,prefix+'b',8059);rollback=Stack(rolled);targets.append(rollback)
            report['projects'].append(rollback.project);report['rollback_restore']=verification
            validate_target(rollback,8059)
            report['selected_endpoint']='http://127.0.0.1:8059'
            report['rollback_rto_seconds']=time.monotonic()-start
            check('snapshot rollback and endpoint switch within four hours',report['rollback_rto_seconds']<=14400)
            check('trial source original approval still pending and source healthy',api.call('/api/runs/'+state['pending_run'],token)['status']=='awaiting_approval' and healthy(source))
            report['passed']=True
        except Exception as e:
            report['error_type']=type(e).__name__
            if isinstance(e,AssertionError):report['failure']=str(e)
            raise
        finally:
            if probe:probe.terminate();probe.wait(timeout=10)
            if handle:handle.close()
            cleanup=[]
            for s in targets:
                try:s.cmd('stop')
                except Exception:cleanup.append(s.project)
            report['cleanup_errors']=cleanup
            if cleanup:report['passed']=False
            report['finished_at']=now().isoformat();report['drill_volumes_preserved']=True
            write(out,report)
        if not report['passed']:raise RuntimeError('Drill cleanup incomplete')
        state['final_drill' if final else 'initial_drill']={'passed':True,'backup_id':backup['id'],'report':str(out),'finished_at':report['finished_at']}
        write(work/'trial-state.json',state)
        if final and status(work,state)['passed']:
            state['completed_at']=now().isoformat();state['completion']=status(work,state)
            write(work/'trial-state.json',state)
            (work/'observer-stop').write_text(now().isoformat(),encoding='utf-8')
        print(json.dumps({'passed':True,'report':str(out),'checks':len(report['checks']),'rpo_seconds':report['rpo_seconds'],'recovery_rto_seconds':report['recovery_rto_seconds'],'rollback_rto_seconds':report['rollback_rto_seconds']}))
        return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True);p.add_argument('--final',action='store_true');a=p.parse_args()
    try:drill(a.work,a.final);return 0
    except Exception as e:print(json.dumps({'passed':False,'error_type':type(e).__name__}));return 1

if __name__=='__main__':raise SystemExit(main())
