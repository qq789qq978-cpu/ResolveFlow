// Deterministic delayed-response tests; no browser, network or third-party packages.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

test('PDF sources use physical pages while Markdown keeps source lines',()=>{
    const context=vm.createContext({document:{getElementById:()=>({}),querySelectorAll:()=>[]},window:{addEventListener(){}},setInterval(){},console});
    // Exercise the shipped formatter without constructing unrelated event handlers.
    const source=fs.readFileSync('frontend/dist/app.js','utf8');
    vm.runInContext(source.slice(source.indexOf('function sourceLabel('),source.indexOf('function renderEvidence(')),context);
    const label=vm.runInContext("sourceLabel({source_type:'pdf',source:'materials.pdf',version:'1',page_start:2,page_end:2,page_label:'i',line_start:8,line_end:9})",context);
    assert.match(label,/PDF 第 2–2 页（物理页码）/);
    assert.doesNotMatch(label,/8–9 行/);
    assert.match(vm.runInContext("sourceLabel({source:'refund.md',version:'2',line_start:7,line_end:9})",context),/第 7–9 行/);
});

function harness() {
    class Element {
        constructor() { this.children=[]; this.value=''; this.hidden=false; this.disabled=false; this.dataset={}; this.classList={toggle(){}}; }
        set textContent(value) { this.text=String(value); this.children=[]; }
        get textContent() { return (this.text||'')+this.children.map(c=>c.textContent).join(''); }
        append(child) { this.children.push(child); }
        replaceChildren(...children) { this.text=''; this.children=children; }
    }
    const html=fs.readFileSync('frontend/dist/index.html','utf8');
    const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
    const nav=['','awaiting_approval','escalated','failed'].map((filter,index)=>{
        const e=new Element(); e.dataset.filter=filter; e.textContent=['全部工单','待审批','人工核查','运行异常'][index]; return e;
    });
    const runs=new Map(['A','B'].map(id=>[id,{id,ticket:'Ticket '+id,order_id:'RF-1002',status:'escalated',state:{},job:{kind:'investigate',attempts:1}}]));
    const holds=[], calls=[], listeners={}; let interval;
    const alerts={available:true,checked_at:'2026-09-23T00:00:00Z',workers_online:1,alerts:[],truncated:false};
    const response=data=>({ok:true,status:200,json:async()=>structuredClone(data)});
    function payload(path,options) {
        if(path==='/config') return {role:options.headers['X-API-Key'],mode:'demo',database:'PostgreSQL',model:'demo'};
        if(path==='/orders') return [{id:'RF-1002',amount:12900,used:true,days:12}];
        if(path==='/metrics') return {workers_online:1,attempts:{total:3,failed:0},runs:{}};
        if(path==='/alerts') return alerts;
        if(path.startsWith('/knowledge')) return {results:[{title:'Policy',source:'refund.md',version:'2',text:path,line_start:1,line_end:2}]};
        if(path.startsWith('/runs?')) return [...runs.values()].filter(r=>!new URLSearchParams(path.split('?')[1]).get('status')||r.status===new URLSearchParams(path.split('?')[1]).get('status'));
        if(path==='/runs') { const r={id:'NEW',ticket:'Created ticket',order_id:'RF-1002',status:'queued',state:{}}; runs.set(r.id,r); return r; }
        const [, , id, action]=path.split('/');
        const row=runs.get(id);
        if(action==='review') Object.assign(row,{status:'closed',review:{resolution:JSON.parse(options.body).resolution}});
        if(action==='retry') row.status='queued';
        if(action==='approval') row.status='approval_queued';
        return row;
    }
    const fetch=(url,options)=>{
        const path=url.slice(4); calls.push({path,method:options.method,key:options.headers['X-API-Key']});
        const hold=holds.find(h=>!h.used&&h.path===path&&h.method===options.method);
        const data=structuredClone(payload(path,options));
        if(hold) { hold.used=true; return new Promise((resolve,reject)=>{hold.release=()=>resolve(response(data)); hold.fail=()=>reject(new TypeError('offline'));}); }
        return Promise.resolve(response(data));
    };
    const context=vm.createContext({document:{hidden:false,getElementById:id=>elements[id],createElement:()=>new Element(),querySelectorAll:()=>nav},
        window:{addEventListener:(name,fn)=>{listeners[name]=fn;}},fetch,AbortController,TypeError,SyntaxError,
        setInterval:fn=>{interval=fn;},setTimeout,clearTimeout,console});
    vm.runInContext(fs.readFileSync('frontend/dist/app.js','utf8'),context);
    const submit=id=>elements[id].onsubmit({preventDefault(){}});
    const login=async role=>{elements.key.value=role;await submit('authform');};
    const hold=(path,method='GET')=>{const h={path,method,used:false};holds.push(h);return h;};
    return {elements,nav,runs,calls,hold,login,submit,alerts,show:id=>vm.runInContext(`show(${JSON.stringify(id)})`,context),poll:()=>interval(),listeners};
}

test('admin alerts show actionable stalled tasks and disappear after recovery',async()=>{
    const h=harness();h.alerts.alerts=[{code:'task_stalled',run_id:'A'}];await h.login('admin');
    assert.match(h.elements.alerts.textContent,/心跳正常不代表任务有进展/);
    assert.match(h.elements.alerts.textContent,/工单 A/);
    h.alerts.alerts=[];await h.poll();assert.match(h.elements.alertstatus.textContent,/当前未发现告警/);
    assert.equal(h.elements.alerts.textContent,'');
});

test('failed alert polling marks the last snapshot unknown rather than recovered',async()=>{
    const h=harness();h.alerts.alerts=[{code:'job_failed',run_id:'A'}];await h.login('admin');
    const hold=h.hold('/alerts');const poll=h.poll();
    for(let i=0;i<30&&!hold.used;i++) await Promise.resolve();
    assert.equal(hold.used,true);hold.fail();await poll;
    assert.match(h.elements.alertstatus.textContent,/状态未知/);assert.match(h.elements.alerts.textContent,/工单 A/);
});

test('a delayed admin alert cannot cross a role switch',async()=>{
    const h=harness();await h.login('admin');const delayed=h.hold('/alerts');const poll=h.poll();
    for(let i=0;i<30&&!delayed.used;i++) await Promise.resolve();
    assert.equal(delayed.used,true);h.elements.logout.onclick();await h.login('operator');delayed.release();await poll;
    assert.equal(h.elements.alertstatus.textContent,'');assert.equal(h.elements.alerts.textContent,'');
    assert.equal(h.elements.monitor.hidden,true);
});

test('a previous role cannot publish a delayed created ticket into the new session',async()=>{
    const h=harness(); await h.login('operator');
    const delayed=h.hold('/runs','POST'); const request=h.submit('createform');
    h.elements.logout.onclick(); await h.login('reviewer'); delayed.release(); await request;
    assert.equal(h.elements.mode.textContent.includes('审批'),true);
    assert.equal(h.elements.detail.hidden,true); assert.equal(h.elements.createpanel.hidden,true);
    assert.equal(h.elements.message.textContent,'已连接，任务在后台持续处理');
});
for(const action of ['review','retry','approval']) test(`${action} completion cannot replace a newly selected ticket`,async()=>{
    const h=harness(); await h.login('admin'); await h.show('A');
    const delayed=h.hold('/runs/A/'+action,'POST');
    h.elements.resolution.value='核查完成'; h.elements.reason.value='人工拒绝';
    const request=h.elements[{review:'closecase',retry:'retry',approval:'reject'}[action]].onclick();
    await h.show('B'); delayed.release(); await request;
    assert.equal(h.elements.description.textContent,'Ticket B');
});
test('a previous filter response cannot replace the latest filter results',async()=>{
    const h=harness(); await h.login('admin');
    const delayed=h.hold('/runs?limit=20&offset=0&status=escalated'); const first=h.nav[2].onclick();
    await h.nav[3].onclick(); delayed.release(); await first;
    assert.equal(h.elements.listtitle.textContent,'运行异常'); assert.equal(h.elements.rows.textContent,'暂无工单');
});
test('out of order detail requests keep the last selected ticket',async()=>{
    const h=harness(); await h.login('admin'); const delayed=h.hold('/runs/A');
    const first=h.show('A'); await h.show('B'); delayed.release(); await first;
    assert.equal(h.elements.description.textContent,'Ticket B');
});
test('old polling detail cannot overwrite a completed mutation',async()=>{
    const h=harness(); await h.login('admin'); await h.show('A');
    const delayed=h.hold('/runs/A'); const poll=h.poll();
    for(let i=0;i<8&&!delayed.used;i++) await Promise.resolve();
    assert.equal(delayed.used,true); h.elements.resolution.value='核查结论已保存';
    await h.elements.closecase.onclick(); delayed.release(); await poll;
    assert.equal(h.elements.detailstatus.textContent,'人工已关闭');
    assert.equal(h.elements.proposal.textContent,'人工核查已结案：核查结论已保存');
});
test('a network failure keeps draft input and polling recovers without replaying writes',async()=>{
    const h=harness(); await h.login('operator'); h.elements.ticket.value='未提交草稿';
    const delayed=h.hold('/runs','POST'); const request=h.submit('createform'); delayed.fail(); await request;
    assert.match(h.elements.connection.textContent,/连接已中断/); assert.match(h.elements.message.textContent,/未能确认提交结果/);
    assert.equal(h.elements.ticket.value,'未提交草稿'); await h.poll();
    assert.match(h.elements.connection.textContent,/连接已恢复/);
    assert.equal(h.calls.filter(c=>c.path==='/runs'&&c.method==='POST').length,1);
});
test('a stale login response cannot restore the older role',async()=>{
    const h=harness(); const delayed=h.hold('/config'); const first=h.login('admin');
    await h.login('reviewer'); delayed.release(); await first;
    assert.match(h.elements.mode.textContent,/审批/); assert.equal(h.elements.monitor.hidden,true);
});
test('reconnection replaces obsolete read-error text while preserving the draft',async()=>{
    const h=harness(); await h.login('operator'); h.elements.ticket.value='保留草稿';
    const delayed=h.hold('/runs?limit=20&offset=0&status='); const refresh=h.elements.refresh.onclick();
    delayed.fail(); await refresh; assert.match(h.elements.message.textContent,/暂时无法连接服务/);
    await h.poll(); assert.match(h.elements.message.textContent,/连接已恢复/);
    assert.equal(h.elements.ticket.value,'保留草稿');
});
test('an old search does not clear the busy state of a new session search',async()=>{
    const h=harness(); await h.login('operator'); h.elements.knowledgequery.value='退款';
    const firstHold=h.hold('/knowledge?query='+encodeURIComponent('退款')); const first=h.submit('knowledgeform');
    h.elements.logout.onclick(); await h.login('reviewer'); h.elements.knowledgequery.value='物流';
    const secondHold=h.hold('/knowledge?query='+encodeURIComponent('物流')); const second=h.submit('knowledgeform');
    firstHold.release(); await first; assert.equal(h.elements.knowledgesubmit.disabled,true);
    secondHold.release(); await second; assert.equal(h.elements.knowledgesubmit.disabled,false);
});
test('pending actions cannot be duplicated after switching away and back',async()=>{
    const h=harness(); await h.login('admin'); await h.show('A'); h.elements.resolution.value='结案';
    const delayed=h.hold('/runs/A/review','POST'); const first=h.elements.closecase.onclick();
    await h.show('B'); await h.show('A'); h.elements.resolution.value='结案'; await h.elements.closecase.onclick();
    assert.equal(h.calls.filter(c=>c.path==='/runs/A/review').length,1); delayed.release(); await first;
});

test('verified quotes and retrieved candidates are visibly distinct and escaped',async()=>{
    const h=harness(); await h.login('operator');
    const source={chunk_id:'refund-v2:abc:0',source:'refund.md',version:'2',line_start:8,line_end:8,text:'<script>alert(1)</script>'};
    h.runs.get('A').state={proposal:{citation_schema:2},evidence:[source],result:{grounding:{usable:true,verified:[{...source,quote:source.text}]}}};
    await h.show('A');
    assert.match(h.elements.citationstatus.textContent,/引用已匹配/);
    assert.match(h.elements.citationquotes.textContent,/<script>alert\(1\)<\/script>/);
    assert.match(h.elements.evidence.textContent,/检索候选原文/);
    assert.equal(h.elements.citationquotes.children[0].children.at(-1).href,'#evidence-0');
    assert.equal(h.elements.evidence.children[0].id,'evidence-0');
    await h.show('B'); assert.equal(h.elements.citationquotes.textContent,'');
});

test('legacy and insufficient evidence cannot display a successful citation check',async()=>{
    const h=harness(); await h.login('operator');
    h.runs.get('A').state={proposal:{citations:['refund-v2']},evidence:[]};
    await h.show('A'); assert.match(h.elements.citationstatus.textContent,/历史工单/);
    h.runs.get('A').state.result={grounding:{usable:false,verified:[]}};
    await h.show('A'); assert.match(h.elements.citationstatus.textContent,/需要人工核查/);
});

test('new proposals never expose an unverified model reason while awaiting validation',async()=>{
    const h=harness(); await h.login('operator');
    h.runs.get('A').state={proposal:{citation_schema:2,reason:'保证999元立即到账',citations:[]}};
    await h.show('A');
    assert.equal(h.elements.proposal.textContent,'处理建议尚未完成依据核验。');
    assert.equal(h.elements.proposal.textContent.includes('999'),false);
});

test('expired policy remains an authentic historical quote without claiming usability',async()=>{
    const h=harness(); await h.login('operator');
    const source={chunk_id:'refund-v2:abc:0',source:'refund.md',version:'2',line_start:8,line_end:8,text:'policy',
        policy:{reason:'expired',review_basis:'demo_fixture',effective_from:'2026-09-21T00:00:00Z',effective_until:'2026-09-22T00:00:00Z'}};
    h.runs.get('A').state={proposal:{citation_schema:2},evidence:[source],result:{grounding:{usable:false,verified:[{...source,quote:source.text}]}}};
    await h.show('A');
    assert.match(h.elements.citationstatus.textContent,/需要人工核查/);
    assert.match(h.elements.citationquotes.textContent,/已过期/);
    assert.match(h.elements.citationquotes.textContent,/演示样例，非人工审核/);
    assert.match(h.elements.evidence.textContent,/截止时刻起不可用/);
});

test('release generation is visible and incompatible releases are not presented as usable',async()=>{
    const h=harness(); await h.login('operator');
    const source={source:'refund.md',version:'2',text:'policy',release:{id:'policy-a',generation:1,refund_rule:{version:'refund-v2'}}};
    h.runs.get('A').state={evidence:[source],result:{grounding:{usable:false,verified:[],release:{valid:false,reason:'release_incompatible_or_corrupt',token:null}}}};
    await h.show('A');
    assert.match(h.elements.citationquotes.textContent,/退款规则不兼容/);
    assert.match(h.elements.evidence.textContent,/policy-a · 变更序号 1 · 退款规则 refund-v2/);
    assert.match(h.elements.citationstatus.textContent,/需要人工核查/);
});
