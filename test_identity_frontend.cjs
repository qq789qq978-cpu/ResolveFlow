const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness() {
    class Element {
        constructor() { this.children=[]; this.value=''; this.hidden=false; this.dataset={}; this.firstChild={textContent:''}; this.classList={toggle(){}}; }
        setAttribute() {}
        set textContent(value) { this.text=String(value ?? ''); this.children=[]; }
        get textContent() { return (this.text||'')+this.children.map(c=>c.textContent).join(''); }
        append(child) { this.children.push(child); }
        replaceChildren(...children) { this.text=''; this.children=children; }
    }
    const html=fs.readFileSync('frontend/dist/index.html','utf8');
    const elements=Object.fromEntries([...html.matchAll(/id="([^"]+)"/g)].map(m=>[m[1],new Element()]));
    const calls=[], holds=[]; let revoked=false, interval;
    const response=(data,status=200)=>({status,ok:status<400,json:async()=>structuredClone(data)});
    const fetch=async(url,options)=>{
        calls.push({url,...options});
        let data={},status=200;
        if(url==='/api/session') {
            const body=JSON.parse(options.body);
            data={token:'opaque-'+body.username,account:{username:body.username,role:body.username,workspace:'alpha'}};
        } else if(revoked) { status=401;data={detail:'expired'}; }
        else if(url==='/api/config') data={role:options.headers.Authorization.replace('Bearer opaque-',''),mode:'demo',model:'demo',database:'PostgreSQL'};
        else if(url==='/api/orders') data=[];
        else if(url.startsWith('/api/runs?')) data=[];
        else if(url==='/api/accounts') data={accounts:[{id:'acct',username:'invited',role:'operator',workspace:'alpha',enabled:1}],workspaces:['alpha','beta']};
        else if(url.startsWith('/api/access-audit')) data=[];
        else if(url==='/api/metrics') data={workers_online:1,attempts:{total:0,failed:0},runs:{}};
        else if(url==='/api/alerts') data={available:true,workers_online:1,alerts:[]};
        const hold=holds.find(h=>h.url===url&&!h.used);
        if(hold) { hold.used=true;return new Promise((resolve,reject)=>{hold.release=()=>resolve(response(data,status));hold.fail=()=>reject(new TypeError('offline'));}); }
        return response(data,status);
    };
    const context=vm.createContext({document:{documentElement:{dataset:{authMode:'personal'}},hidden:false,
        getElementById:id=>elements[id],createElement:()=>new Element(),querySelectorAll:()=>[]},
        window:{addEventListener(){}},fetch,AbortController,TypeError,SyntaxError,
        setTimeout,clearTimeout,setInterval:fn=>{interval=fn;},console});
    vm.runInContext(fs.readFileSync('frontend/dist/app.js','utf8'),context);
    vm.runInContext(fs.readFileSync('frontend/dist/personal.js','utf8'),context);
    return {elements,calls,context,login:async(name)=>{elements.username.value=name;elements.key.value='synthetic-password';await elements.authform.onsubmit({preventDefault(){}});},
        logout:()=>elements.logout.onclick(),poll:()=>interval(),revoke:()=>{revoked=true;},
        hold:url=>{const h={url};holds.push(h);return h;}};
}

test('personal login sends password once, keeps bearer only in memory and displays scope',async()=>{
    const h=harness(); await h.login('operator');
    assert.equal(h.elements.userlabel.hidden,false);assert.equal(h.elements.key.value,'');
    assert.match(h.elements.mode.textContent,/operator · 工作区 alpha/);
    assert.equal(h.calls[0].url,'/api/session');
    for(const call of h.calls.slice(1)) {
        assert.equal(call.headers.Authorization,'Bearer opaque-operator');
        assert.equal(call.headers['X-API-Key'],undefined);
        assert.equal(call.body,undefined);
    }
    await h.logout();assert.equal(h.calls.at(-1).url,'/api/logout');
    assert.equal(h.elements.workspace.hidden,true);assert.match(h.elements.message.textContent,/会话已撤销/);
});

test('maintenance UI has accounts and audit but never requests business data',async()=>{
    const h=harness();await h.login('manager');
    assert.equal(h.elements.accountpanel.hidden,false);assert.equal(h.elements.accesspanel.hidden,false);
    assert.equal(h.elements.workspace.hidden,true);
    assert.deepEqual(h.calls.map(c=>c.url),['/api/session','/api/accounts','/api/access-audit']);
    await h.poll();assert.equal(h.calls.length,3);
    await h.logout();assert.equal(h.elements.accountrows.textContent,'');assert.equal(h.elements.accessrows.textContent,'');
});

test('revocation clears visible business data and prompts a fresh login',async()=>{
    const h=harness();await h.login('operator');h.revoke();await h.poll();
    assert.equal(h.elements.workspace.hidden,true);assert.equal(h.elements.login.hidden,false);
    assert.match(h.elements.message.textContent,/会话已失效/);
    assert.equal(vm.runInContext('key',h.context),'');
});

test('unconfirmed logout retains token to retry server-side revocation',async()=>{
    const h=harness();await h.login('operator');const hold=h.hold('/api/logout');
    const logout=h.logout();assert.equal(hold.used,true);hold.fail();await logout;
    assert.equal(vm.runInContext('key',h.context),'opaque-operator');
    assert.match(h.elements.message.textContent,/未能确认提交结果/);
    await h.logout();assert.equal(vm.runInContext('key',h.context),'');
});

test('delayed personal login cannot install an old bearer over a newer login',async()=>{
    const h=harness();const hold=h.hold('/api/session');const first=h.login('operator');
    await h.login('reviewer');hold.release();await first;
    assert.equal(vm.runInContext('key',h.context),'opaque-reviewer');
    assert.equal(h.elements.createpanel.hidden,true);
});

test('delayed maintenance data cannot reappear after logout',async()=>{
    const h=harness();const hold=h.hold('/api/accounts');const login=h.login('manager');
    for(let i=0;i<30&&!hold.used;i++) await Promise.resolve();
    assert.equal(hold.used,true);await h.logout();hold.release();await login;
    assert.equal(h.elements.accountrows.textContent,'');assert.equal(h.elements.accountpanel.hidden,true);
});
