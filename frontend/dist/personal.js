// Included only by the personal gateway. Tokens remain inside app.js memory.
// The legacy local workbench retains its existing role-code behavior.
function personalInitialize() {
    $('userlabel').hidden = false;
    $('username').required = true;
    $('keylabel').firstChild.textContent = '个人密码';
    $('key').placeholder = '输入个人密码';
    $('key').autocomplete = 'current-password';
    $('loginhint').textContent = '仅限受邀账号。退出会撤销当前会话；刷新页面须重新登录。';
    $('logout').textContent = '退出登录';
}

async function loadAccounts(owner = session) {
    const result = await api('/accounts', undefined, owner);
    $('accountrows').replaceChildren(); $('accountworkspace').replaceChildren();
    for (const workspace of result.workspaces) {
        const option = add($('accountworkspace'), 'option', workspace); option.value = workspace;
    }
    for (const account of result.accounts) {
        const row = add($('accountrows'), 'article');
        add(row, 'strong', `${account.username} · ${account.workspace || '账号维护'} · ${account.role} · ${account.enabled ? '启用' : '停用'}`);
        add(row, 'small', account.id);
        if (account.role === 'manager') continue;
        const select = add(row, 'select'); select.setAttribute('aria-label', account.username + '的角色');
        for (const role of ['operator', 'reviewer', 'admin']) {
            const option = add(select, 'option', {operator:'运营',reviewer:'审批',admin:'管理员'}[role]);
            option.value = role; option.selected = role === account.role;
        }
        const password = add(row, 'input'); password.type = 'password'; password.autocomplete = 'new-password';
        password.placeholder = '重置密码（至少12位）'; password.setAttribute('aria-label', account.username + '的新密码');
        const update = async body => {
            try { await api('/accounts/' + account.id, body, owner); await loadAccounts(owner); message('账号已更新，旧会话已撤销'); }
            catch (error) { report(error, owner); }
        };
        add(row, 'button', '修改角色').onclick = () => update({role: select.value});
        add(row, 'button', account.enabled ? '停用' : '启用').onclick = () => update({enabled: !account.enabled});
        add(row, 'button', '重置密码').onclick = () => { const value = password.value; password.value = ''; return update({password: value}); };
    }
}

async function loadAccessAudit(owner = session, older = false) {
    const before = older ? $('auditolder').dataset.before : '';
    const rows = await api('/access-audit' + (before ? '?before=' + before : ''), undefined, owner);
    $('accessrows').replaceChildren();
    for (const row of rows) add($('accessrows'), 'p', `${new Date(row.created_at * 1000).toLocaleString()} · ${row.actor || '匿名'} · ${row.workspace || '账号维护'} · ${row.action} · ${row.target || ''} · ${row.status}`);
    if (!rows.length) add($('accessrows'), 'p', '暂无记录');
    $('auditolder').disabled = rows.length < 100;
    $('auditolder').dataset.before = rows.length ? rows[rows.length - 1].id : '';
}

if (personal) {
    personalInitialize();
    $('accountform').onsubmit = async event => {
        event.preventDefault(); const owner = session;
        const body = {username: $('accountname').value, password: $('accountpassword').value,
            role: $('accountrole').value, workspace: $('accountworkspace').value};
        $('accountpassword').value = '';
        try { await api('/accounts', body, owner); await loadAccounts(owner); message('已创建个人账号'); }
        catch (error) { report(error, owner); }
    };
    $('auditrefresh').onclick = () => loadAccessAudit().catch(error => report(error));
    $('auditolder').onclick = () => loadAccessAudit(session, true).catch(error => report(error));
}
