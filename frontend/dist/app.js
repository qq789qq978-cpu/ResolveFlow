const $ = id => document.getElementById(id);
let key = '', role = '', session = 0, filter = '', offset = 0;
let current = null, selectedId = null, selection = 0, listRequest = 0, searchRequest = 0;
let polling = false, disconnected = false;
const requests = new Set(), pending = new Set();
const names = {queued:'已排队',approval_queued:'审批已入队',retrying:'等待自动重试',auto_rejected:'自动拒绝退款',auto_approved:'自动批准',running:'调查中',awaiting_approval:'等待审批',escalated:'转人工核查',answered:'已答复',refunded:'已模拟退款',already_refunded:'已拦截重复退款',rejected:'已拒绝',closed:'人工已关闭',failed:'调查失败'};
const terminal = new Set(['auto_rejected','answered','refunded','already_refunded','rejected','closed','failed','escalated','awaiting_approval']);
const message = text => $('message').textContent = text;
const pendingEvidenceMessage = '处理建议尚未完成依据核验。';
function policyLabel(policy) {
    if (!policy) return '历史快照未记录审核与有效期；再次执行时会重新检查。';
    const statuses = {active:'有效',draft:'草稿',in_review:'待审核',approved:'审核通过',rejected:'审核不通过',revoked:'已撤销',expired:'已过期',not_yet_effective:'尚未生效',demo_only:'仅限演示',review_in_future:'审核时间异常',missing_or_invalid_metadata:'审核信息缺失或无效',content_changed:'原文已变更',policy_removed_or_changed:'政策已移除或变更'};
    const basis = policy.review_basis === 'demo_fixture' ? '演示样例，非人工审核' : policy.review_basis === 'operator_attested' ? '维护人员声明已审核' : '未提供审核来源';
    return `${statuses[policy.reason] || '状态未知'} · ${basis} · 生效 ${policy.effective_from || '未设置'} · 截止 ${policy.effective_until || '未设截止时间'}（截止时刻起不可用）`;
}
function releaseLabel(release) {
    if (!release) return '历史记录未绑定发布版本；恢复时需重新核查。';
    if (release.valid === false) return '未发布、原文不一致或退款规则不兼容，当前不能采用。';
    const token = release.token || release;
    return `${token.id} · 变更序号 ${token.generation} · 退款规则 ${token.refund_rule?.version || '未知'}`;
}
function sourceLabel(hit) {
    const where = hit.source_type === 'pdf'
        ? `PDF 第 ${hit.page_start}–${hit.page_end} 页（物理页码）`
        : `第 ${hit.line_start}–${hit.line_end} 行`;
    return `${hit.source || hit.id} · 版本 ${hit.version || hit.id} · ${where}`;
}
function renderEvidence(state) {
    const result = state.result?.grounding;
    $('citationquotes').replaceChildren();
    $('evidence').replaceChildren();
    if (result?.release) add($('citationquotes'),'p',`核验时发布版本：${releaseLabel(result.release)}`);
    $('citationstatus').textContent = result
        ? (result.usable ? '引用已匹配政策原文；问题适用性仍须结合订单和处理结论核对。' : '引用缺失或证据不足，需要人工核查；部分原文不代表完整处理依据。')
        : (state.proposal ? '历史工单：旧版引用未按片段核验，原处理记录保留。' : '等待调查与引用核对。');
    for (const quote of result?.verified || []) {
        const box = add($('citationquotes'),'article','','evidence-chunk');
        add(box,'strong','已核对的引用原文');
        add(box,'div',sourceLabel(quote));
        add(box,'small',`核验时状态：${policyLabel(quote.policy)}`);
        add(box,'p',quote.quote);
        const index = (state.evidence || []).findIndex(e=>e.chunk_id===quote.chunk_id);
        if (index >= 0) add(box,'a','查看完整片段').href = '#evidence-'+index;
    }
    (state.evidence || []).forEach((e,index)=>{
        const box=add($('evidence'),'div','','evidence-chunk');
        box.id='evidence-'+index;
        add(box,'strong',e.title||e.id);
        add(box,'small','检索候选原文（不等于已采用的处理依据）');
        add(box,'div',sourceLabel(e));
        add(box,'small',`检索时状态：${policyLabel(e.policy)}`);
        add(box,'small',`检索时发布版本：${releaseLabel(e.release)}`);
        add(box,'p',e.text);
        if(e.chunk_id)add(box,'small',`片段 ${e.chunk_id}`);
    });
}
class StaleResponse extends Error {}
function connectionLost() {
    disconnected = true;
    $('connection').hidden = false;
    $('connection').textContent = role ? '连接已中断，正在自动重连。填写的内容已保留；提交结果不明时，请先查看工单记录，勿重复提交。' : '暂时无法连接服务，请稍后重新连接。';
}
function connectionRestored() {
    if (!disconnected) return;
    disconnected = false;
    $('connection').hidden = false;
    $('connection').textContent = '连接已恢复，工单状态会自动更新。';
    if ($('message').textContent === '暂时无法连接服务，页面会自动重试读取。') message('连接已恢复，正在更新工单状态。');
}
function report(error, owner = session) {
    if (owner === session && !(error instanceof StaleResponse)) message(error.message);
}
async function api(path, body, owner = session) {
    if (owner !== session) throw new StaleResponse();
    const controller = new AbortController();
    requests.add(controller);
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
        const response = await fetch('/api' + path, {
            method: body === undefined ? 'GET' : 'POST',
            headers: {'X-API-Key':key,'Content-Type':'application/json'},
            body: body === undefined ? undefined : JSON.stringify(body), signal:controller.signal
        });
        if (owner !== session) throw new StaleResponse();
        if (response.status >= 500) throw new TypeError('Service unavailable');
        const data = await response.json();
        if (owner !== session) throw new StaleResponse();
        connectionRestored();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail));
        return data;
    } catch (error) {
        if (owner !== session) throw new StaleResponse();
        if (error.name === 'AbortError' || error instanceof TypeError || error instanceof SyntaxError) {
            connectionLost();
            throw new Error(body === undefined ? (role ? '暂时无法连接服务，页面会自动重试读取。' : '暂时无法连接服务，请稍后重试连接。') : '未能确认提交结果。请恢复连接后先查看工单记录，勿重复提交。');
        }
        throw error;
    } finally { clearTimeout(timeout); requests.delete(controller); }
}
function resetSession() {
    session++; selection++; listRequest++; searchRequest++;
    for (const controller of requests) controller.abort();
    requests.clear(); pending.clear();
    key = ''; role = ''; current = null; selectedId = null; filter = ''; offset = 0;
    disconnected = false; $('connection').hidden = true;
    $('workspace').hidden = true; $('detail').hidden = true; $('monitor').hidden = true;
    $('approval').hidden = $('review').hidden = true;
    $('login').hidden = false; $('logout').hidden = true;
    $('mode').textContent = '等待连接'; $('metrics').textContent = '';
    $('rows').replaceChildren(); $('knowledgeresults').replaceChildren();
    for (const id of ['key','ticket','reason','resolution','knowledgequery']) $(id).value = '';
    for (const id of ['submit','accept','reject','closecase','retry','knowledgesubmit']) $(id).disabled = false;
    $('listtitle').textContent = '全部工单';
    document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active',b.dataset.filter === ''));
}
function add(parent, tag, text, cls) {
    const element = document.createElement(tag); element.textContent = text;
    if (cls) element.className = cls;
    parent.append(element); return element;
}
async function refresh(owner = session) {
    const version = ++listRequest, pageOffset = offset, pageFilter = filter;
    const rows = await api(`/runs?limit=20&offset=${pageOffset}&status=${encodeURIComponent(pageFilter)}`, undefined, owner);
    if (owner !== session || version !== listRequest) return;
    $('rows').replaceChildren();
    if (!rows.length) add($('rows'),'tr','暂无工单');
    for (const row of rows) {
        const tr = add($('rows'),'tr',''), td = add(tr,'td',row.ticket);
        add(td,'div',row.id.slice(0,8)); add(tr,'td',row.order_id);
        add(add(tr,'td',''),'span',names[row.status] || row.status,'badge');
        add(tr,'td',new Date(row.created_at).toLocaleString());
        const button = add(add(tr,'td',''),'button','查看','secondary');
        button.onclick = () => show(row.id).catch(error => report(error,owner));
    }
    $('page').textContent = `第 ${pageOffset / 20 + 1} 页`;
    $('prev').disabled = pageOffset === 0; $('next').disabled = rows.length < 20;
}
function renderDetails(r,preserveInputs=false){current=r;$('detail').hidden=false;$('detailstatus').textContent=names[r.status]||r.status;$('description').textContent=r.ticket;const s=r.state||{};$('checks').replaceChildren();for(const check of s.result?.checks||[])add($('checks'),'p',`${check.passed?'满足':'不满足'}：${check.name}`);if(s.result?.decision_source)add($('checks'),'p',`决策方式：${{automatic:'自动处理',human:'人工审批',manual_required:'等待人工审批',system:'系统分流'}[s.result.decision_source]||s.result.decision_source} · ${s.result.policy_version||'旧版规则'}`);$('facts').textContent=s.order?`${s.order.id||'未知订单'} · ¥${((s.order.amount||0)/100).toFixed(2)} · 签收 ${s.order.days??'未知'} 天 · ${s.order.used?'已使用':'未使用'}`:'调查尚无订单结果';$('proposal').textContent=r.status==='closed'&&r.review?`人工核查已结案：${r.review.resolution}`:s.result?.reason||(s.proposal?.citation_schema===2?pendingEvidenceMessage:s.proposal?.reason)||r.error||'尚无处理建议';$('conflicts').hidden=!s.conflicts?.length;$('conflicts').textContent=(s.conflicts||[]).join('；');renderEvidence(s);$('trace').replaceChildren();if(s.skill)add($('trace'),'p',`已加载技能：${s.skill.name} · ${s.skill.sha256.slice(0,12)}`);for(const t of s.trace||[])add($('trace'),'p',[t.node,t.transport,t.tool,(t.tools||[]).join(' / '),names[t.status]||t.status].filter(Boolean).join(' · '));$('approval').hidden=r.status!=='awaiting_approval'||!['reviewer','admin'].includes(role);$('review').hidden=r.status!=='escalated'||!['reviewer','admin'].includes(role);$('audit').textContent=r.approval?`审批记录：${r.approval.approved?'同意':'拒绝'} · ${r.approval.reason}`:r.review?`人工处理：${r.review.resolution}`:'';if(!preserveInputs){$('reason').value='';$('resolution').value='';}$('jobinfo').textContent=r.job?`任务类型：${r.job.kind==='approval'?'审批执行':'调查'} · 已完成尝试 ${r.job.attempts} 次${r.job.last_error?' · '+r.job.last_error:''}`:'';$('retry').hidden=role!=='admin'||r.status!=='failed';}
function updateActionButtons() {
    const prefix = `${session}:${selectedId}:`;
    $('accept').disabled = $('reject').disabled = pending.has(prefix + 'approval');
    $('closecase').disabled = pending.has(prefix + 'review');
    $('retry').disabled = pending.has(prefix + 'retry');
}
function render(row, preserveInputs = false) {
    const changed = current?.id === row.id && current.status !== row.status;
    renderDetails(row,preserveInputs); updateActionButtons();
    if (changed && terminal.has(row.status)) message(`当前工单：${names[row.status] || row.status}`);
}
async function show(id) {
    const owner = session, token = ++selection;
    selectedId = id; current = null; $('detail').hidden = true;
    const row = await api('/runs/' + id, undefined, owner);
    if (owner === session && token === selection && selectedId === id) {
        render(row); message(`当前工单：${names[row.status] || row.status}`);
    }
}
$('authform').onsubmit = async event => {
    event.preventDefault();
    const code = $('key').value;
    resetSession(); key = code;
    const owner = session;
    try {
        const config = await api('/config',undefined,owner);
        const orders = await api('/orders',undefined,owner);
        if (owner !== session) return;
        role = config.role;
        $('order').replaceChildren();
        for (const order of orders) {
            const option = add($('order'),'option',`${order.id} · ¥${order.amount/100} · ${order.used?'已使用':'未使用'} / ${order.days} 天`);
            option.value = order.id;
        }
        $('createpanel').hidden = role === 'reviewer'; $('monitor').hidden = role !== 'admin';
        $('mode').textContent = `${config.mode==='live'?'真实模型':'规则演示'} · ${config.model} · ${config.database} · ${{operator:'运营',reviewer:'审批',admin:'管理员'}[role]}`;
        $('login').hidden = true; $('workspace').hidden = false; $('logout').hidden = false;
        message('已连接，任务在后台持续处理');
        await refresh(owner); await monitor(owner);
    } catch (error) { if (owner === session && !role) key = ''; report(error,owner); }
};
$('logout').onclick = () => { resetSession(); message('已退出当前角色'); };
$('createform').onsubmit = async event => {
    event.preventDefault();
    const owner = session, token = selection, operation = `${owner}:create`;
    if (pending.has(operation)) return;
    pending.add(operation); $('submit').disabled = true; message('正在提交工单…');
    try {
        const row = await api('/runs',{ticket:$('ticket').value,order_id:$('order').value},owner);
        if (owner !== session) return;
        if (token === selection) {
            selection++; selectedId = row.id;
            render(row); message(`工单已提交：${names[row.status] || row.status}`);
        }
        await refresh(owner);
    } catch (error) { report(error,owner); }
    finally { pending.delete(operation); if (owner === session) $('submit').disabled = false; }
};
async function act(kind, body) {
    if (!current || current.id !== selectedId) return;
    const owner = session, id = current.id, operation = `${owner}:${id}:${kind}`;
    if (pending.has(operation)) return;
    pending.add(operation);
    const token = ++selection;
    updateActionButtons();
    try {
        const row = await api(`/runs/${id}/${kind}`,body,owner);
        if (owner !== session) return;
        if (token === selection && selectedId === id) {
            selection++; render(row);
            message(terminal.has(row.status) ? `当前工单：${names[row.status]}` : '操作已记录，等待后台处理。');
        }
        await refresh(owner);
    } catch (error) { if (token === selection && selectedId === id) report(error,owner); }
    finally { pending.delete(operation); if (owner === session) updateActionButtons(); }
}
function decide(approved) {
    const reason = $('reason').value.trim();
    if (reason.length < 2) return message('请填写至少两个字的审批理由');
    return act('approval',{approved,reason});
}
$('accept').onclick = () => decide(true);
$('reject').onclick = () => decide(false);
$('closecase').onclick = () => {
    const resolution = $('resolution').value.trim();
    if (resolution.length < 2) return message('请填写核查结论');
    return act('review',{resolution});
};
$('retry').onclick = () => act('retry',{});
function refreshView() { const owner = session; return refresh(owner).catch(error => report(error,owner)); }
document.querySelectorAll('[data-filter]').forEach(button => button.onclick = () => {
    filter = button.dataset.filter; offset = 0; $('listtitle').textContent = button.textContent;
    document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active',b===button));
    if (key) return refreshView();
});
$('refresh').onclick = refreshView;
$('prev').onclick = () => { offset = Math.max(0,offset-20); return refreshView(); };
$('next').onclick = () => { offset += 20; return refreshView(); };
async function monitor(owner = session) {
    if (role !== 'admin' || owner !== session) return;
    const metrics = await api('/metrics',undefined,owner);
    if (owner !== session || role !== 'admin') return;
    $('metrics').textContent = `在线 Worker：${metrics.workers_online} · 执行尝试：${metrics.attempts.total} · 失败尝试：${metrics.attempts.failed} · 平均 ${metrics.attempts.avg_ms||0} ms · P95 ${Math.round(metrics.attempts.p95_ms||0)} ms · 当前失败工单：${metrics.runs.failed||0}`;
}
async function poll() {
    if (!key || !role || polling || document.hidden) return;
    polling = true;
    const owner = session;
    try {
        await refresh(owner);
        if (owner !== session) return;
        const id = selectedId, token = selection;
        if (id) {
            const row = await api('/runs/' + id,undefined,owner);
            if (owner === session && token === selection && selectedId === id) render(row,true);
        }
        await monitor(owner);
    } catch (error) { report(error,owner); }
    finally { polling = false; }
}
setInterval(poll,4000);
window.addEventListener('online',poll);
window.addEventListener('offline',() => { if (key) connectionLost(); });
$('knowledgeform').onsubmit = async event => {
    event.preventDefault();
    const owner = session, token = ++searchRequest, query = $('knowledgequery').value.trim();
    $('knowledgesubmit').disabled = true;
    try {
        const result = await api('/knowledge?query=' + encodeURIComponent(query),undefined,owner);
        if (owner !== session || token !== searchRequest) return;
        $('knowledgeresults').replaceChildren();
        if (result.release) add($('knowledgeresults'),'p',`当前发布版本：${releaseLabel(result.release)}`);
        for (const item of result.policies || []) add($('knowledgeresults'),'p',`${item.title} · ${policyLabel(item.policy)}`);
        if (!result.results.length) add($('knowledgeresults'),'p','没有检索到相关政策，请转人工核查。');
        for (const hit of result.results) {
            const box = add($('knowledgeresults'),'article','','evidence-chunk');
            add(box,'h3',hit.title); add(box,'small',sourceLabel(hit));
            add(box,'small',policyLabel(hit.policy));
            add(box,'p',hit.text);
        }
    } catch (error) { if (token === searchRequest) report(error,owner); }
    finally { if (owner === session && token === searchRequest) $('knowledgesubmit').disabled = false; }
};
// Optional WebMCP navigation only; approval and refund actions stay explicit.
if (document.modelContext?.registerTool) {
    const lifetime = new AbortController();
    window.addEventListener('pagehide',() => lifetime.abort(),{once:true});
    Promise.resolve(document.modelContext.registerTool({
        name:'resolveflow_open_ticket',description:'打开已存在的工单并展示详情。需要先在页面授权。不会审批或退款。',
        inputSchema:{type:'object',properties:{run_id:{type:'string'}},required:['run_id'],additionalProperties:false},
        annotations:{readOnlyHint:false,untrustedContentHint:true},
        execute:async input => {
            if (!key || !role) throw Error('请先在页面授权');
            if (!input || typeof input.run_id !== 'string' || !/^[0-9a-f-]{36}$/i.test(input.run_id)) throw Error('无效工单编号');
            await show(input.run_id);
            if (current?.id !== input.run_id) throw Error('当前页面已切换，请重新选择工单');
            return {id:current.id,status:current.status};
        }
    },{signal:lifetime.signal})).catch(() => {});
}
