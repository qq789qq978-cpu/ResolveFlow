"""Refresh current trial evidence and handoff without claiming future dates passed."""
import argparse
from datetime import datetime,timedelta
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.local_trial import load,read,write,status,summarize_observer,HK

def checkpoint(work):
    work,state,s=load(work);result=status(work,state);evidence=Path(state['evidence'])
    observation=summarize_observer(work,state)
    stage='已完成' if result['passed'] else '执行中，尚未完成'
    day=datetime.fromisoformat(result['checked_at']).astimezone(HK).date().isoformat();count=result['successful_days']
    drill=state.get('final_drill') or state.get('initial_drill');detail=read(drill['report']) if drill else None
    earliest=(datetime.fromisoformat(state['started_at'])+timedelta(days=6)).astimezone(HK).isoformat()
    link=evidence.relative_to(ROOT).as_posix()
    timing=(f"成功演练{len(detail['checks'])}项，备份年龄/RPO={detail['rpo_seconds']:.1f}秒，恢复RTO={detail['recovery_rto_seconds']:.1f}秒、回退RTO={detail['rollback_rto_seconds']:.1f}秒。" if detail else '恢复/回退演练尚无成功证据。')
    text=(f'当前状态（{day}，5.7{stage}）：独立试运行resolveflow-accounts-trial57，入口http://127.0.0.1:8057，'
          f'真实成功日期{count}个、成功联合备份及独立目录副本{len(state["backups"])}份；{timing}'
          f'连续七个香港日期及至少六整天经过时间均为硬闸门，首个窗口最早{earliest}后收口，漏跑不补写。'
          f'见[5.7记录]({link}/REPORT.md)、[实时状态]({link}/status.json)和[操作手册](TRIAL.md)。'
          '每小时本聊天自动检查resolveflow-5-7；本机观察进程每60秒采样，关机/缺失采样如实记为未观测，不称连续在线或生产SLA。'
          '原主环境/8056保留，8053/8054仍停止；所有派生故障演练停止并保留卷。'
          '新容量部署alpha监控期望已修正为2，旧8056未升级。首轮分钟边界测试失败、需审批重复请求演练预期错误保留，修正后通过。'
          f'AC-04/10/11{"通过" if result["passed"] else "仍为部分完成/待验收"}；'
          f'下一项具体操作：{"保存最终验收本地提交并暂停自动检查；5.8仍待用户指令" if result["passed"] else "继续真实每日合成流程、备份及缺口核查，闸门满足后从最新副本执行--final演练，再本地收口"}。'
          '当前实现/测试/文档/证据按本地断点提交保留，具体SHA及未提交修改看git；不推送、不开始5.8。'
          '仍有效：同目录/0元/demo/BM25/无付费模型与真实资金/无公网或外部通知；保留.env、历史数据与卷、审批/checkpoint/退款幂等/权限及SQL空格，不读旧会话和排障备份。AC-07外部沙箱、4.4暂缓，5.8统一推送同SHA CI。')
    for name in ('PROJECT_HANDOFF.md','EXECUTION_PLAN.md','ROADMAP.md'):
        p=ROOT/name;old=p.read_text(encoding='utf-8');a=old.index('当前状态（');b=old.index('\n\n',a)
        old=old[:a]+text+old[b:]
        if name=='EXECUTION_PLAN.md':
            lines=old.splitlines();lines=[f'|5.7|本地试运行、独立恢复与回退演练|{stage}；真实日期{count}/7，副本{len(state["backups"])}份；[记录]({link}/REPORT.md)；不推送|' if line.startswith('|5.7|') else line for line in lines];old='\n'.join(lines)+'\n'
        if name=='ROADMAP.md':old=old.replace('下一项5.7尚未开始',f'5.7{stage}，真实日期以顶部记录为准').replace('5.7本地7天试运行与恢复回退、5.8统一交付待指令执行','5.7本地7天试运行与恢复回退进度见顶部，5.8统一交付仍待指令')
        p.write_text(old,encoding='utf-8')
    days=[read(p) for p in sorted((evidence/'days').glob('*.json'))]
    table='\n'.join(f'|{d["day"]}|{"通过" if d["passed"] else "失败"}|{d["backup_id"]}|[证据](days/{d["day"]}.json)|' for d in days)
    detail_link=Path(drill['report']).relative_to(evidence).as_posix() if drill else None
    report=f'''# 5.7 本地试运行记录

当前：**{stage}**。真实成功日期 **{count}/7**；开始于 {state['started_at']}，最早 {earliest} 后满足时间闸门。基线 e670f60，本轮不推送、无新远程CI；不能将首日或七次当天重跑称为七天完成。

## 已验证

新试运行入口 http://127.0.0.1:8057 ，固定三Worker、demo/BM25。原待审批工单为 `{state['pending_run']}`，始终保留在源试运行，审批恢复只在派生副本演练。业务流程验证两个工作区自动模拟退款、重新提交的幂等台账、跨区拒绝、无活动告警与原待审批可读。原主环境/8056不作为故障目标。

{timing} {('[演练明细]('+detail_link+')') if detail_link else ''} RPO仅证明该次模拟事故的备份年龄；七天备份间隔仍单独核对。RTO包含恢复、原审批/checkpoint与重复请求核验。回退采用同一冻结镜像和原快照，记录客户端8058→8059入口切换；不是跨版本schema降级。

|实际香港日期|当天结果|联合备份/独立副本编号|证据|
|---|---|---|---|
{table}

成功联合备份与受保护独立目录副本共 {len(state['backups'])} 份，见 [状态](status.json) 及 backups 目录。全部历史备份保留；同为D盘，异地/整盘损坏保护未验证。每小时检查、每天真实流程，满20小时补备份；超过24小时的实际间隔不隐藏。备份维护窗口会短暂停服务。

宿主入口观察已保存 {observation['samples']} 次采样，出现 {len(observation['unobserved_gaps'])} 段未观测区间、{len(observation['unavailable_samples'])} 次不可用采样；见[观察汇总](observation-summary.json)。未观测不等于已证实离线，也不等于在线。容器monitor独立观测；没有外部整机离线探测或消息通知。

## 修复与失败证据

新容量部署原先alpha有两个Worker、monitor/API期望仍默认1，已在生成配置中显式设为2，beta为1；实际演练停掉单个alpha Worker后观察告警及恢复。旧8056未自动改配置。

既有限流测试跨真实分钟边界，首轮44通过/1失败；固定单元测试时钟后复测通过，生产规则未变。见[首次报告](unit-first.xml)和[说明](unit-first-note.json)。首个恢复演练错误期待需审批订单的新请求直接返回幂等结果；实际仍待审批且台账仅一条，见[first-drill-diagnosis.json](first-drill-diagnosis.json)。修正为再次审批后核验幂等；所有演练明细保留在drills目录。队列年龄与连续失败事件是明确注入的夹具，不冒充实际Worker执行失败；入口和单Worker停机为真实容器故障。

本轮47项针对性测试通过，见 [unit-verified.xml](unit-verified.xml)；没有把重叠复测加总，也未宣称本步重新跑完整PG或前端回归。生产镜像沿用5.6，本步改动在宿主试运行工具、新部署配置与测试。首日原主环境/8056及保护文件核对见[preservation-day1.json](preservation-day1.json)；当前代码与67个5.6镜像运行文件一致，原业务、身份、checkpoint保留，正常Worker心跳时间继续变化。

## 待完成与接续

AC-04/10/11{'已有七天及最终演练证据，通过' if result['passed'] else '尚未全部通过'}。必须有七个真实连续香港日期、至少七份完整备份与独立副本、符合24小时备份间隔，再以最新备份执行最终 --final 演练。失败/缺日期保持进行中；不能提前提交“5.7全部完成”。

本聊天每小时自动检查（resolveflow-5-7），命令和暂停/开机恢复方法见[TRIAL.md](../../TRIAL.md)。电脑和Docker未运行时不会自动产生证据；无人观测的时间保留空白。只有最终通过才停止观察/自动检查并本地收口。5.8尚未开始，AC-07外部支付沙箱和4.4仍暂缓。
'''
    (evidence/'REPORT.md').write_text(report,encoding='utf-8')
    p=ROOT/'docs/deployment/scope.json';scope=read(p)
    scope['version']='5.7-local-verified' if result['passed'] else '5.7-local-trial-in-progress'
    scope['stage_rules']['current_step_only']='5.7'
    scope['implementation_5_7']={'status':'passed' if result['passed'] else 'in_progress_real_dates_required',
        'project':s.project,'url':result['url'],'successful_dates':result['observed_successful_dates'],
        'backups':len(state['backups']),'initial_drill':state.get('initial_drill'),
        'final_drill':state.get('final_drill'),'independent_directory':True,'offsite':False,
        'evidence':link+'/REPORT.md','automation_id':'resolveflow-5-7','remote_ci_for_this_step':False}
    if result['passed']:
        for ac in scope['acceptance']:
            if ac['id'] in ('AC-04','AC-10','AC-11'):ac.update(status='passed',completed_steps=ac['steps'],pending_steps=[],evidence=link+'/REPORT.md',environment='isolated_local_daily_trial_not_24x7')
    write(p,scope)
    for name in ('README.md','docs/deployment/REQUIREMENTS.md'):
        p=ROOT/name;old=p.read_text(encoding='utf-8');prefix='../../' if name.startswith('docs/') else ''
        marker='<!-- trial57-current -->';end='<!-- /trial57-current -->'
        entry=f'{marker}\n5.7{stage}：真实成功日期{count}/7，联合备份与独立目录副本{len(state["backups"])}份。试运行8057，原8056保留。见[记录]({prefix}{link}/REPORT.md)和[手册]({prefix}TRIAL.md)。不推送；5.8未开始，外部沙箱继续暂缓。\n{end}'
        if marker in old:a=old.index(marker);b=old.index(end,a)+len(end);old=old[:a]+entry+old[b:]
        else:a=old.index('\n\n');old=old[:a]+'\n\n'+entry+old[a:]
        p.write_text(old,encoding='utf-8')
    print(json.dumps({'stage':stage,'days':count,'backups':len(state['backups']),'passed':result['passed']},ensure_ascii=False))

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--work',type=Path,required=True);a=p.parse_args();checkpoint(a.work)
