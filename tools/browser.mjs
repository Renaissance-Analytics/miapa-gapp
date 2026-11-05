// Owned native-browser fixture only. No installed driver, arbitrary debug URL or human profile.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { closeBrowserThroughDebugger, readDebuggingPortFile, ownedProfileRoot } from "./browser-profile.mjs";

let phase = "fixture_input", failedPhase, cleanupVerified = false, checkpoint;
let startupEvidence = {}, launchState = "starting", launchExit;
let authEvidence = {};
const authResponses = {};
let surfaceEvidence = {};
const surfaceResponses = {};
process.on("uncaughtException", error => {
  // Isolated synthetic fixture diagnostics stay in ignored local failure logs;
  // CI artifacts contain only the fixed phase/cleanup report below.
  console.error(error?.stack || 'Native browser fixture failed');
  console.log(JSON.stringify({ checks: 0, reason: failedPhase || phase, cleanup: cleanupVerified, ...startupEvidence,
    ...((failedPhase || phase) === 'setup_chat' ? { checkpoint } : {}),
    ...(['admin_users', 'work_views', 'agent_shell'].includes(failedPhase || phase) ? surfaceEvidence : {}),
    ...(['login', 'logout_viewer'].includes(failedPhase || phase) ? { checkpoint, ...authEvidence } : {}) }));
  process.exitCode = 1;
});

let input = "";
for await (const chunk of process.stdin) input += chunk;
const fixture = JSON.parse(input);
assert.match(fixture.origin, /^http:\/\/127\.0\.0\.1:\d+$/);
assert.match(fixture.build_id, /^[0-9a-f]{64}$/);
phase = "browser_binary";
const binary = ["C:/Program Files/Google/Chrome/Application/chrome.exe", "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"].find(existsSync);
assert.ok(binary, "Native browser prerequisite");
phase = "profile_parent";
const parent = ownedProfileRoot(fixture.profile_root);
const profile = mkdtempSync(join(parent, "maipa-ci-browser-"));
assert.equal(dirname(realpathSync.native(profile)), parent);
phase = "browser_launch";
const child = spawn(binary, ["--headless=new", "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
  "--disable-extensions", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
  { windowsHide: true, stdio: "ignore" });
let closed = false;
child.on("exit", code => { closed = true; launchExit = code; launchState = "exited"; });
let launchError;
child.on("error", error => { launchError = error; launchState = "spawn_error"; });
let debugOrigin, socket, evidence;
const pending = new Map();
let sequence = 0;
const exceptions = [];
const intakeListResponses = [];
const passed = [];
let holdComposerSends = false;
let failClientTableRead = false;
const pausedComposerSends = [];
const draftTrace=[];
let sendRequests=0,sendBeforeClick=0,lastSendId=null;
const record = id => { assert.ok(!passed.includes(id)); passed.push(id); };
const progressPhases = new Set(['fixture_input', 'browser_binary', 'profile_parent', 'browser_launch',
  'debug_endpoint', 'service_storage', 'ui_composition', 'admin_users', 'client_catalog', 'agent_shell', 'workflow_editor',
  'anonymous', 'release_controls', 'theme', 'keyboard_divider', 'mobile_panels', 'login', 'logout_viewer',
  'authenticated_scope', 'transport_csrf', 'intake_recovery', 'intake_facts', 'intake_history', 'intake_scope',
  'authority', 'first_run', 'setup_chat', 'setup_isolation', 'work_views', 'layout', 'layout_resume',
  'preferences', 'browser_exceptions', 'cleanup']);
let cleanupCheckpoint = 'not_started';
let stopStatus = null, stopError = null, rootAliveAfterStop = null;
const cleanupCheckpoints = new Set(['not_started', 'browser_close', 'socket_close', 'pending_close', 'stop_tree', 'browser_exit', 'endpoint', 'profile_remove', 'complete']);
const recordProgress = () => {
  // Closed fixture metadata survives an outer watchdog without DOM/user data.
  try { writeFileSync('artifacts/ci/browser-progress.json', JSON.stringify({
    phase: progressPhases.has(phase) ? phase : 'unknown',
    completed_cases: Number.isInteger(passed.length) && passed.length <= 45 ? passed.length : 0,
    cleanup_verified: cleanupVerified === true,
    cleanup_checkpoint: cleanupCheckpoints.has(cleanupCheckpoint) ? cleanupCheckpoint : 'not_started',
    stop_status: Number.isInteger(stopStatus) ? stopStatus : null,
    stop_error: ['ENOENT','ETIMEDOUT','EACCES','EPERM','ESRCH','EINVAL'].includes(stopError) ? stopError : null,
    root_alive_after_stop: typeof rootAliveAfterStop === 'boolean' ? rootAliveAfterStop : null,
  })); } catch { /* Progress cannot replace the required final result. */ }
};
const progressTimer = setInterval(recordProgress, 1000);
progressTimer.unref();
recordProgress();
async function until(predicate, label) {
  for (let attempt = 0; attempt < 150; attempt++) {
    const result = await predicate();
    if (result) return result;
    await delay(100);
  }
  throw new Error(`Timeout: ${label}`);
}
async function evaluate(expression) {
  const response = await command("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if(response.exceptionDetails)console.error("Owned evaluation diagnostic",JSON.stringify({text:response.exceptionDetails.text,description:response.exceptionDetails.exception?.description?.slice(0,180)}));
  assert.ok(!response.exceptionDetails, "Browser evaluation failure");
  return response.result.value;
}
async function checkHeaderViewport(){
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
  const geometry=await evaluate("[...document.querySelectorAll('.header-shortcuts button,.app-header .build-updates button,.app-header .account-menu>summary')].map(b=>{const r=b.getBoundingClientRect();return {label:b.getAttribute('aria-label')||b.textContent,left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:innerWidth};})");
  writeFileSync('artifacts/ci/header-geometry.json',JSON.stringify(geometry));
  assert.ok(geometry.every(r=>r.left>=0&&r.right<=r.width&&r.top>=0&&r.bottom<=54),'Common header controls fit: '+JSON.stringify(geometry));
}
async function openRunDetails() {
  const expanded=()=>evaluate("[...document.querySelectorAll('.workflow-execution button')].find(b=>b.textContent==='Run details')?.getAttribute('aria-expanded')==='true'");
  if(!await expanded())await click('Run details');
  await until(expanded,'shared run details acknowledged open');
}
async function checkChatViewport(width, height) {
  await command('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width <= 700 });
  if (await evaluate("document.querySelector('.conversation')?.inert")) await click('Conversation');
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  const geometry = await evaluate(`(() => {
    const rect = selector => { const r=document.querySelector(selector).getBoundingClientRect();
      return {top:r.top,bottom:r.bottom,left:r.left,right:r.right,height:r.height}; };
    const input=rect('#maipa-chat-message');
    const send=rect('.chat-composer button[aria-label="Send"]');
    const controls=rect('.chat-controls');
    const conversation=rect('.conversation');
    const pane=document.querySelector('.conversation').parentElement;
    return {input,send,controls,conversation,paneHeight:pane.clientHeight,paneScrollHeight:pane.scrollHeight,
      width:innerWidth,height:innerHeight,documentHeight:document.documentElement.scrollHeight};
  })()`);
  const screenshot = await command('Page.captureScreenshot', { format: 'png' });
  writeFileSync('artifacts/ci/chat-viewport.png', Buffer.from(screenshot.data, 'base64'));
  writeFileSync('artifacts/ci/chat-viewport.json', JSON.stringify(geometry));
  assert.ok(geometry.input.top >= 0 && geometry.input.bottom <= height && geometry.input.right <= width,
    `Chat input must fit ${width}x${height}: ${JSON.stringify(geometry)}`);
  assert.ok(geometry.send.top >= 0 && geometry.send.bottom <= height && geometry.send.right <= width,
    'Send must remain fully visible without scrolling the conversation pane');
  assert.ok(geometry.controls.bottom <= geometry.conversation.bottom + 1,
    'Chat controls must stay inside the conversation');
  assert.ok(geometry.paneScrollHeight <= geometry.paneHeight + 1,
    'Only transcript/setup content scrolls, never the whole conversation pane');
}
async function checkTranscriptTail(label) {
  await evaluate('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))');
  const geometry = await evaluate(`(() => {
    const list=document.querySelector('[data-react-fancy-virtual-list]');
    const last=[...list.querySelectorAll('.chat-message')].at(-1);
    const r=list.getBoundingClientRect(),m=last.getBoundingClientRect();
    const body=list.parentElement;
    return {label:${JSON.stringify(label)},following:list.dataset.reactFancyVirtualListFollowing,
      viewport:{top:r.top,bottom:r.bottom,height:list.clientHeight},last:{top:m.top,bottom:m.bottom},
      scrollTop:list.scrollTop,scrollHeight:list.scrollHeight,bottomGap:list.scrollHeight-list.clientHeight-list.scrollTop,
      bodyHeight:body.clientHeight,bodyScrollHeight:body.scrollHeight,
      end:Number(list.querySelector('[data-react-fancy-virtual-list-start]').dataset.reactFancyVirtualListStart)+list.querySelectorAll('[data-react-fancy-virtual-row-id]').length,total:Number(list.dataset.reactFancyVirtualListRows),
      rows:[...list.querySelectorAll('[data-react-fancy-virtual-row-id]')].map(el=>({height:el.getBoundingClientRect().height,content:el.firstElementChild.getBoundingClientRect().height}))};
  })()`);
  writeFileSync('artifacts/ci/chat-tail.json',JSON.stringify(geometry));
  surfaceEvidence.chat_tail_checks ??= []; surfaceEvidence.chat_tail_checks.push(geometry);
  writeFileSync('artifacts/ci/chat-tail-checks.json',JSON.stringify(surfaceEvidence.chat_tail_checks));
  const screenshot=await command('Page.captureScreenshot',{format:'png'});
  writeFileSync('artifacts/ci/chat-tail.png',Buffer.from(screenshot.data,'base64'));
  assert.equal(geometry.following,'true','Latest reply must still be followed');
  assert.equal(geometry.end,geometry.total,'Following must render the actual final message');
  assert.ok(geometry.last.bottom <= geometry.viewport.bottom + 1, `Latest reply must be visible after ${label}: ${JSON.stringify(geometry)}`);
  assert.ok(geometry.bottomGap <= 1, `Following must reach actual scroll bottom after ${label}: ${JSON.stringify(geometry)}`);
  assert.ok(geometry.bodyScrollHeight <= geometry.bodyHeight + 1, 'Transcript owns scrolling without a second clipped scroll container');
}
function command(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { pending.delete(id); reject(Error("CDP command deadline")); }, 15_000);
    pending.set(id, { resolve, reject, timer, method });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function click(label) {
  if (['Dark', 'Light', 'System', 'Sign out'].includes(label)
      && !await evaluate("document.querySelector('.account-menu')?.open")) await click('Account and appearance');
  if (label === 'Account setup' && await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='Account setup')?.getAttribute('aria-expanded')==='true'")) return;
  const point = await until(() => evaluate(`(() => { const b = [...document.querySelectorAll('button, summary')].find(b => (b.getAttribute('aria-label') || b.textContent.trim()) === ${JSON.stringify(label)}); if (!b || b.disabled || b.closest('[inert]')) return null; b.scrollIntoView({block:'center'}); const r=b.getBoundingClientRect(); const x=r.x+r.width/2,y=r.y+r.height/2; const hit=document.elementFromPoint(x,y); return r.width && r.height && hit && b.contains(hit) ? {x,y} : null; })()`), `Visible enabled button ${label}`);
  assert.ok(point, `Visible button ${label}`);
  if(label==='Send')sendBeforeClick=sendRequests;
  await command("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...point });
  await command("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...point });
}

async function waitForAgent(type) {
  try {
  let acceptedRevision=0,acceptedId;
  await until(async()=>{
    if(sendRequests<=sendBeforeClick||!lastSendId)return false;
    acceptedId=lastSendId;
    const result=await evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();const r=await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}});if(r.status!==200)throw Error('Owned chat read refused');const c=await r.json();const lastUser=c.messages.filter(m=>m.role==='user').at(-1);return {done:lastUser?.id===${JSON.stringify(acceptedId)}&&c.phase==='ready'&&c.messages.at(-1)?.role==='assistant'&&c.messages.at(-1)?.state==='complete'&&c.ui?.composition?.schema.type===${JSON.stringify(type)},revision:c.revision};})()`);
    if(result.done)acceptedRevision=result.revision;return result.done;
  },'current submitted agent turn completed a shared composition');
  await until(() => evaluate(`document.querySelector('.chat-status [role=status]')?.textContent==='Ready'&&Number(document.querySelector('[data-chat-revision]')?.dataset.chatRevision)>=${acceptedRevision}`), 'stream acknowledged current completed agent turn');
  } catch (error) {
    // Dedicated synthetic fixture only, fixed classifications/counts in PRIVATE
    // failure log. No transcript, identifiers, credentials or raw DOM/error text.
    console.error('Synthetic shared-composition diagnostic', JSON.stringify(await evaluate(`(async()=>{
      const s=await(await fetch('/api/session/')).json();const c=await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json();
      const a=document.querySelector('.chat-controls [role=alert]')?.textContent??'';
      return {phase:c.phase,messages:c.messages?.length,revision:c.revision,uiRevision:c.ui?.revision??null,
        errorKind:a.includes('disconnected')?'stream':a.includes('changed')?'conflict':a?'unavailable':'none',
        lastMessageState:c.messages?.at(-1)?.state??null,
        reconnectNoteReceived:c.messages?.some(m=>m.role==='user'&&m.text==='Present a note after reconnect')??false,
        responseFailures:[...new Set(performance.getEntriesByType('resource').filter(e=>e.name.endsWith('/api/agent/chat/')).map(e=>e.responseStatus).filter(n=>n>=400))]};})()`)));
    throw error;
  }
}
async function requestComponent(prompt, type) {
  await chooseAgent();
  if (await evaluate("matchMedia('(max-width:700px)').matches && document.querySelector('.has-composition') && document.querySelector('.focus-panel')")) await click('Conversation');
  await until(() => evaluate("!!document.querySelector('#maipa-chat-message')"), 'live chat composer');
  await evaluate("document.querySelector('#maipa-chat-message').focus();true");
  await command('Input.dispatchKeyEvent', {type:'keyDown',key:'a',windowsVirtualKeyCode:65,modifiers:2});
  await command('Input.dispatchKeyEvent', {type:'keyUp',key:'a',windowsVirtualKeyCode:65,modifiers:2});
  await command('Input.insertText', {text:prompt});
  await click('Send');
  await waitForAgent(type);
  if (await evaluate("matchMedia('(max-width:700px)').matches")) await click('Shared work');
  const screenshot = await command('Page.captureScreenshot', { format: 'png' });
  writeFileSync('artifacts/ci/agent-shell-preview.png', Buffer.from(screenshot.data, 'base64'));
}

async function chooseAgent() {
  await until(() => evaluate("!!document.querySelector('#maipa-agent-provider option[value=codex]')"), 'registered agent choices');
  if (!await evaluate("document.getElementById('maipa-agent-provider').value")) {
    await select('maipa-agent-provider', 'codex');
    await until(() => evaluate("document.getElementById('maipa-agent-provider').value==='codex' && !document.getElementById('maipa-agent-provider').disabled"), 'explicit saved provider choice');
  }
}

async function typeChat(text) {
  await evaluate("document.querySelector('#maipa-chat-message').focus();true");
  await command('Input.dispatchKeyEvent', {type:'keyDown',key:'a',windowsVirtualKeyCode:65,modifiers:2});
  await command('Input.dispatchKeyEvent', {type:'keyUp',key:'a',windowsVirtualKeyCode:65,modifiers:2});
  await command('Input.insertText', {text});
}

async function preferencesReady() {
  await until(() => evaluate("!!document.querySelector('.agent-workspace') && document.querySelector('[data-preferences-state]')?.dataset.preferencesState === 'ready'"), 'saved preferences ready');
}
async function manageSavedViews() {
  await click('Manage saved views');
  // Fancy positions a newly mounted portal before removing its invisible state.
  await until(()=>evaluate("(() => {const field=document.getElementById('maipa-view-title');if(!field||field.disabled||getComputedStyle(field).visibility!=='visible')return false;const r=field.getBoundingClientRect();return r.width>0&&r.height>0&&document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===field;})()"),'saved-view controls positioned and ready');
}
async function fill(id, value) {
  assert.equal(await evaluate(`(() => { const field=document.getElementById(${JSON.stringify(id)}); if(!field || field.tagName!=='INPUT')return false; field.focus(); return document.activeElement===field; })()`), true);
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await command('Input.insertText', { text: value });
  await until(() => evaluate(`document.getElementById(${JSON.stringify(id)})?.value === ${JSON.stringify(value)}`), 'native input accepted');
}
async function select(id, value) {
  const navigation = await evaluate(`(() => { const field=document.getElementById(${JSON.stringify(id)});
    if (!field || field.tagName!=='SELECT' || field.disabled) return null;
    field.scrollIntoView({block:'center'}); field.focus();
    const options=[...field.options].filter(o=>!o.disabled), target=options.findIndex(o=>o.value===${JSON.stringify(value)});
    return {target, last:options.length-1, current:options.findIndex(o=>o.value===field.value)}; })()`);
  assert.ok(navigation && navigation.target >= 0, 'Selectable recorded option');
  // Commit one native selection when choosing an endpoint or adjacent option.
  // Intermediate account changes immediately lock this authority-bound control.
  const {target, last, current} = navigation;
  const keys = target===0 ? [['Home',36]] : target===last ? [['End',35]]
    : Array.from({length:Math.abs(target-current)},()=>target>current?['ArrowDown',40]:['ArrowUp',38]);
  for (const [key, code] of keys) {
    await command('Input.dispatchKeyEvent', { type: 'keyDown', key, windowsVirtualKeyCode: code });
    await command('Input.dispatchKeyEvent', { type: 'keyUp', key, windowsVirtualKeyCode: code });
  }
  await until(() => evaluate(`document.getElementById(${JSON.stringify(id)})?.value === ${JSON.stringify(value)}`), 'native select change');
}
try {
  const { port, browserPath } = await until(() => {
    if (launchError || closed) throw Error("Native browser launch failed");
    const file = join(profile, 'DevToolsActivePort');
    if (!existsSync(file)) return false;
    launchState = 'devtools_file';
    // File creation can precede its final write. Never parse a partial record
    // as a ready browser, or connect to an unvalidated endpoint.
    return readDebuggingPortFile(file);
  }, "complete native browser connection record");
  assert.match(port, /^\d{1,5}$/);
  assert.ok(+port > 0 && +port < 65536);
  assert.match(browserPath, /^\/devtools\/browser\/[a-zA-Z0-9-]+$/);
  debugOrigin = `http://127.0.0.1:${port}`;
  phase = "debug_endpoint";
  const version = await (await fetch(`${debugOrigin}/json/version`, { signal: AbortSignal.timeout(5000) })).json();
  assert.match(version.Browser, /^(?:HeadlessChrome|Chrome|Edg)\/[\d.]+$/);
  assert.equal(version.webSocketDebuggerUrl, `ws://127.0.0.1:${port}${browserPath}`);
  const target = await (await fetch(`${debugOrigin}/json/new?about:blank`, { method: "PUT", signal: AbortSignal.timeout(5000) })).json();
  assert.ok(target.webSocketDebuggerUrl.startsWith(`ws://127.0.0.1:${port}/devtools/page/`));
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  socket.addEventListener("message", event => {
    const message = JSON.parse(event.data);
    if(message.method==='Network.requestWillBeSent'&&message.params.request.url===fixture.origin+'/api/agent/chat/'&&message.params.request.method==='POST'){
      let c;try{c=JSON.parse(message.params.request.postData??'{}');}catch{}
      if(c?.action==='send'&&typeof c.request_id==='string'){sendRequests++;lastSendId=c.request_id;}
    }
    if (message.method === 'Fetch.requestPaused') {
      const paused = message.params;
      let body;
      try { body = JSON.parse(paused.request.postData ?? 'null'); } catch { /* Not a JSON send. */ }
      if (failClientTableRead && paused.request.url === fixture.origin + '/api/intake/catalog/') {
        failClientTableRead = false;
        void command('Fetch.failRequest', { requestId: paused.requestId, errorReason: 'Failed' }).catch(() => exceptions.push(true));
      } else if (holdComposerSends && paused.request.method === 'POST' && body?.action === 'send')
        pausedComposerSends.push({ requestId: paused.requestId, body });
      else void command('Fetch.continueRequest', { requestId: paused.requestId }).catch(() => exceptions.push(true));
    }
    if (message.method === "Runtime.exceptionThrown") exceptions.push(true);
    if (message.method === 'Network.responseReceived' && ['admin_users', 'work_views', 'agent_shell'].includes(phase)) {
      for (const [path, key] of [['/api/session/', 'session'], ['/api/agent/chat/', 'chat'], ['/api/admin/users/', 'admin']]) {
        if (message.params.response.url === fixture.origin + path) surfaceResponses[key] = message.params.response.status;
      }
    }
    if (message.method === "Network.responseReceived" && message.params.response.url === `${fixture.origin}/api/intake/engagements/`)
      intakeListResponses.push(message.params.response.status);
    if (message.method === 'Network.responseReceived' && phase === 'intake_facts'
        && message.params.response.url.startsWith(fixture.origin + '/api/intake/entities/')
        && message.params.response.url.endsWith('/facts/')) surfaceResponses.intake = message.params.response.status;
    if (message.method === 'Network.responseReceived' && ['login', 'logout_viewer'].includes(phase)) {
      for (const [path, key] of [['/api/session/', 'session'], ['/api/session/login/', 'login'],
        ['/api/session/logout/', 'logout'], ['/api/workspace/', 'workspace']]) {
        if (message.params.response.url === fixture.origin + path) authResponses[key] = message.params.response.status;
      }
    }
    if (message.id && pending.has(message.id)) {
      const entry = pending.get(message.id); pending.delete(message.id); clearTimeout(entry.timer);
      if (message.error) entry.reject(Error(`CDP ${entry.method} rejected: ${String(message.error.message).slice(0, 200)}`)); else entry.resolve(message.result);
    }
  });
  socket.addEventListener('message',event=>{
    const m=JSON.parse(event.data),p=m.params;if(!p)return;
    if(m.method==='Network.requestWillBeSent'&&p.request.url.startsWith(fixture.origin+'/api/agent/drafts/')){
      let c={};try{c=JSON.parse(p.request.postData??'{}');}catch{}
      draftTrace.push({id:p.requestId,kind:'request',method:p.request.method,expected:c.expected_revision,pending:!!c.pending,text_length:c.draft?.text?.length});
    }
    if(m.method==='Network.requestWillBeSent'&&p.request.url.startsWith(fixture.origin+'/api/workflows/')){
      let c={};try{c=JSON.parse(p.request.postData??'{}');}catch{}
      draftTrace.push({kind:'workflow-request',method:p.request.method,publish:p.request.url.endsWith('/publish/'),expected:c.expected_revision});
    }
    if(m.method==='Network.responseReceived'&&p.response.url.startsWith(fixture.origin+'/api/workflows/'))draftTrace.push({kind:'workflow-status',publish:p.response.url.endsWith('/publish/'),status:p.response.status});
    if(m.method==='Network.requestWillBeSent'&&p.request.url===fixture.origin+'/api/workspace/views/'){
      let c={};try{c=JSON.parse(p.request.postData??'{}');}catch{}
      draftTrace.push({kind:'view-request',method:p.request.method,expected:c.expected_revision,action:c.action});
    }
    if(m.method==='Network.responseReceived'&&p.response.url===fixture.origin+'/api/workspace/views/')draftTrace.push({kind:'view-status',status:p.response.status});
    if(m.method==='Network.responseReceived'&&p.response.url===fixture.origin+'/api/agent/chat/'){
      void command('Network.getResponseBody',{requestId:p.requestId}).then(v=>{try{const c=JSON.parse(v.body);draftTrace.push({kind:'chat-response',status:p.response.status,error:c.error?.code,phase:c.phase,messages_array:Array.isArray(c.messages)});}catch{}}).catch(()=>{});
    }
    if(m.method==='Network.responseReceived'&&p.response.url.startsWith(fixture.origin+'/api/agent/drafts/')){
      draftTrace.push({id:p.requestId,kind:'status',status:p.response.status});
      void command('Network.getResponseBody',{requestId:p.requestId}).then(v=>{try{const c=JSON.parse(v.body);draftTrace.push({id:p.requestId,kind:'response',status:p.response.status,revision:c.revision,error:c.error?.code,pending:!!c.pending,accepted:c.accepted,text_length:c.draft?.text?.length});}catch{}}).catch(()=>{});
    }
  });
  await command("Page.enable"); await command("Runtime.enable"); await command("Network.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: fixture.origin });
  if (fixture.layout_resume) {
    phase = 'layout_resume';
    await until(() => evaluate("!!document.getElementById('maipa-username')"), 'fresh-profile sign in');
    await until(() => evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')"), 'fresh-profile update notes');
    await click('Got it');
    await fill('maipa-username', fixture.username);
    await fill('maipa-password', fixture.password);
    await click('Sign in');
    await until(() => evaluate("!!document.querySelector('.agent-workspace') && document.querySelector('[data-layout-state]')?.dataset.layoutState === 'ready'"), 'fresh-profile saved layout');
    const restored = await evaluate("(async()=>({value:document.querySelector('[role=separator]').getAttribute('aria-valuenow'),layout:await(await fetch('/api/workspace/layout/')).json()}))()");
    assert.equal(restored.layout.user_id, fixture.user_id);
    assert.equal(restored.layout.split_basis_points, fixture.expected_points);
    assert.equal(Number(restored.value), fixture.expected_points / 100);
    const restoredViews=await evaluate("(async()=>await(await fetch('/api/workspace/views/')).json())()");
    assert.deepEqual(restoredViews,fixture.expected_views);
    record('layout-fresh-browser-restore');
    await preferencesReady();
    const preferences = await evaluate("(async()=> (await fetch('/api/workspace/preferences/')).json())()");
    assert.equal(preferences.user_id, fixture.user_id);
    assert.equal(preferences.theme, 'dark');
    assert.equal(preferences.active_view, 'workspace-guide');
    await until(() => evaluate("document.getElementById('maipa-theme-dark')?.getAttribute('aria-pressed') === 'true' && !!document.querySelector('.chat-only')"), 'fresh-profile appearance and conversation restored');
    assert.equal(await evaluate("getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim()"), '#111714');
    record('preferences-fresh-browser-restore');
    await until(()=>evaluate("!!document.querySelector('button[aria-label=\"Open saved view Agent saved review\"]')"),'fresh-login personal pin');
    await click('Open saved view Agent saved review');
    await until(()=>evaluate("document.querySelector('.agent-commentary')?.textContent.includes('Synthetic agent saved definition')"),'fresh-login pin opens under current session');
    await click('Close contextual panel');
    await click('Open saved view Current client overview');
    await until(()=>evaluate("!!document.querySelector('.client-table [data-react-fancy-table]')"),'fresh-login column definition loads current authorized clients');
    assert.deepEqual(await evaluate("[...document.querySelectorAll('.client-table th')].map(n=>n.textContent)"), ['Entities','Client']);
    assert.equal(await evaluate("(async()=>{const current=await(await fetch('/api/intake/catalog/')).json();return JSON.stringify([...document.querySelectorAll('.client-table [data-client-id]')].map(n=>n.dataset.clientId))===JSON.stringify(current.engagements.map(row=>row.id));})()"),true);
    await click('Close contextual panel');
  } else if (fixture.first_run) {
    phase = 'first_run';
    await until(() => evaluate("!!document.getElementById('maipa-admin-username')"), 'first-run administrator form');
    await until(() => evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')"), 'first-run update notes');
    await click('Got it');
    assert.equal(await evaluate("document.querySelectorAll('[aria-label=\"Create local administrator\"] input').length"), 3);
    assert.equal(await evaluate("!!document.getElementById('maipa-setup-code')"), false);
    await fill('maipa-admin-username', fixture.username);
    await fill('maipa-admin-password', fixture.password);
    await fill('maipa-admin-confirmation', fixture.password);
    await click('Create administrator');
    await until(() => evaluate("!!document.querySelector('.agent-workspace')"), 'created administrator session');
    await until(() => evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')"), 'new-account update notes');
    await click('Got it');
    const admin = await evaluate("(async()=>({workspace:await(await fetch('/api/workspace/')).json(),setup:await(await fetch('/api/setup/')).json()}))()");
    assert.equal(admin.workspace.role, 'administrator');
    assert.equal(admin.setup.setup_required, false);
    assert.equal(await evaluate("!!document.querySelector('[name=\"password-confirmation\"]')"), false);
    record('local-first-run-admin');
    await until(()=>evaluate("!!document.querySelector('.header-shortcuts [aria-label=\"User management\"]')"),'administrator header shortcut');
    await click('User management');
    await until(()=>evaluate("!!document.querySelector('.admin-users')"),'administrator component opens without a model turn');
    assert.equal(await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return (await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json()).messages.length;})()"),0);
    await command('Emulation.setDeviceMetricsOverride',{width:320,height:480,deviceScaleFactor:1,mobile:true});
    await checkHeaderViewport();
    await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
    await click('Close contextual panel');

    phase = 'setup_chat';
    await chooseAgent();
    checkpoint = 'setup_mobile';
    await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await click('Account setup');
    await until(() => evaluate("!document.querySelector('[data-pane=agent]').inert && document.querySelector('[data-pane=work]').inert"), 'mobile account conversation');
    const atStep = step => until(() => evaluate(`document.querySelector('[data-setup-step]')?.dataset.setupStep === ${JSON.stringify(step)} && document.querySelector('[data-setup-step]').getAttribute('aria-busy') === 'false'`), 'saved setup prompt');
    checkpoint = 'setup_display_name';
    await atStep('display_name');
    await fill('maipa-setup-answer', 'Synthetic onboarding owner');
    await click('Send answer');
    checkpoint = 'setup_workspace';
    await atStep('workspace');
    checkpoint = 'setup_resume';
    await command('Page.reload');
    await until(() => evaluate("!![...document.querySelectorAll('button, summary')].find(b=>b.textContent==='Account setup')"), 'resumed authenticated shell');
    await click('Account setup');
    await atStep('workspace');
    assert.ok(await evaluate("document.querySelector('[aria-label=\"Account setup conversation\"]').innerText.includes('Synthetic onboarding owner')"));
    checkpoint = 'setup_engagement';
    await select('maipa-setup-answer', 'new'); await click('Send answer'); await atStep('engagement_name');
    checkpoint = 'setup_entity';
    await fill('maipa-setup-answer', 'Synthetic onboarding engagement'); await click('Send answer'); await atStep('entity_name');
    checkpoint = 'setup_currency';
    await fill('maipa-setup-answer', 'Synthetic onboarding entity'); await click('Send answer'); await atStep('currency');
    checkpoint = 'setup_reporting_basis';
    await fill('maipa-setup-answer', 'USD'); await click('Send answer'); await atStep('reporting_basis');
    checkpoint = 'setup_effective_from';
    await select('maipa-setup-answer', 'unknown'); await click('Send answer'); await atStep('effective_from');
    assert.match(await evaluate("document.getElementById('maipa-setup-answer').value"), /^\d{4}-\d{2}-\d{2}$/);
    checkpoint = 'setup_review';
    await click('Send answer'); await atStep('review');
    assert.ok(await evaluate("document.querySelector('[aria-label=\"Account setup review\"]').innerText.includes('Unknown for now')"));
    const before = await evaluate("(async()=>({setup:await(await fetch('/api/onboarding/')).json(),clients:await(await fetch('/api/intake/engagements/')).json()}))()");
    assert.deepEqual(before.clients.engagements, []);
    checkpoint = 'setup_confirm';
    await click('Confirm and save setup'); await atStep('complete');
    const saved = await evaluate("(async()=>{const setup=await(await fetch('/api/onboarding/')).json();return {setup,entity:await(await fetch('/api/intake/entities/'+setup.entity_id+'/')).json()};})()");
    assert.equal(saved.entity.name, 'Synthetic onboarding entity');
    assert.equal(saved.entity.facts.currency.value, 'USD');
    assert.equal(saved.entity.facts.reporting_basis.knowledge, 'unknown');
    assert.equal(saved.entity.facts.legal_form.knowledge, 'unknown');
    assert.equal(saved.setup.provider_ready, false);
    checkpoint = 'setup_stale';
    const stale = await evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();return (await fetch('/api/onboarding/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Maipa-Setup':${JSON.stringify(before.setup.setup_id)}},body:JSON.stringify({expected_revision:${before.setup.revision},action:'reply',answer:'confirm'})})).status;})()`);
    assert.equal(stale, 409);
    checkpoint = 'setup_open_facts';
    await click('Open saved client facts'); await click('Send'); await waitForAgent('Maipa.ClientFacts'); await click('Shared work');
    await until(() => evaluate("document.querySelector('[data-pane=agent]').inert && !document.querySelector('[data-pane=work]').inert && !!document.querySelector('[data-entity-id]')"), 'confirmed client in work panel');
    assert.equal(await evaluate("document.querySelector('[data-fact-key=currency] dd').textContent"), 'USD');
    record('account-chat-resume-and-confirm');
    phase = 'client_catalog';
    await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await preferencesReady();
    const beforeCatalog = await evaluate("(async()=>({preferences:await(await fetch('/api/workspace/preferences/')).json(),layout:await(await fetch('/api/workspace/layout/')).json(),setup:await(await fetch('/api/onboarding/')).json()}))()");
    await evaluate("window.maipaCatalogChat=document.querySelector('[data-pane=agent]'); true");
    const beforeSharedFill = await evaluate("(async()=>await(await fetch('/api/intake/catalog/')).json())()");
    await requestComponent('Fill shared client draft', 'Maipa.ClientCatalog');
    const catalogReady = revision => until(() => evaluate(`document.querySelector('[data-catalog-state]')?.dataset.catalogState === 'ready' && ${revision === undefined ? 'true' : `document.querySelector('[data-catalog-revision]')?.dataset.catalogRevision === '${revision}'`}`), 'current client administration');
    const catalogState = () => evaluate("(async()=>{const id=document.getElementById('catalog-client')?.value;return(await fetch('/api/intake/catalog/'+(id?'?engagement_id='+id:''))).json();})()");
    await catalogReady();
    await until(() => evaluate("document.querySelector('[data-form-phase]')?.dataset.formPhase==='ready' && document.getElementById('catalog-new-client').value==='Synthetic agent pending client' && document.getElementById('catalog-initial-entity').value==='Synthetic agent pending entity'"), 'native accepted values visible in controlled Fancy form');
    assert.deepEqual(await catalogState(), beforeSharedFill);
    assert.equal(await evaluate("(async()=>{const session=await(await fetch('/api/session/')).json();const chat=await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':session.user.id,'X-Maipa-Agent-Activity':'1'}})).json();const legacy=await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':session.user.id}})).json();window.maipaFormReceipt=chat.activity.id;return chat.activity.action==='form_set_values'&&chat.activity.form_id===document.querySelector('[data-shared-form]').dataset.sharedForm&&!('activity' in legacy)&&!JSON.stringify(chat.activity).includes('Synthetic agent');})()"), true);
    const originalCatalog = await catalogState();
    await fill('catalog-new-client', 'Synthetic catalog client');
    await fill('catalog-initial-entity', 'Synthetic catalog entity');
    await until(() => evaluate("document.querySelector('[data-form-phase]')?.dataset.formPhase==='ready'"), 'human pending input acknowledged before explicit business submission');
    assert.equal(await evaluate("(async()=>{const session=await(await fetch('/api/session/')).json();const chat=await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':session.user.id,'X-Maipa-Agent-Activity':'1'}})).json();const draft=await(await fetch('/api/agent/accounts/'+chat.ui.account_id+'/ui/'+chat.ui.id+'/forms/shared-root/',{headers:{'X-Maipa-Agent-Actor':session.user.id}})).json();return draft.values.name==='Synthetic catalog client'&&draft.values.entity_name==='Synthetic catalog entity'&&chat.activity.id===window.maipaFormReceipt;})()"), true);
    await click('Create client'); await catalogReady(originalCatalog.revision+1);
    let catalog = await catalogState();
    const catalogGroup = catalog.selected_engagement_id, catalogEntity = catalog.entities[0].id;
    assert.equal(catalog.engagements.find(row => row.id === catalogGroup).name, 'Synthetic catalog client');
    assert.equal(catalog.entities[0].name, 'Synthetic catalog entity');
    assert.notEqual(catalogGroup, originalCatalog.selected_engagement_id);
    await fill(`catalog-name-client-${catalogGroup}`, 'Synthetic catalog client renamed');
    await click('Save client name for Synthetic catalog client'); await catalogReady(originalCatalog.revision+2);
    await fill(`catalog-name-entity-${catalogEntity}`, 'Synthetic catalog entity renamed');
    await click('Save entity name for Synthetic catalog entity'); await catalogReady(originalCatalog.revision+3);
    await fill('catalog-add-entity', 'Synthetic catalog second entity');
    await click('Add entity'); await catalogReady(originalCatalog.revision+4);
    catalog = await catalogState();
    assert.deepEqual(catalog.entities.map(row => row.name), ['Synthetic catalog entity renamed', 'Synthetic catalog second entity']);
    assert.deepEqual(catalog.events.slice(0, 4).map(row => row.action), ['create_entity', 'rename_entity', 'rename_engagement', 'create_engagement']);
    const concurrentCatalog = await evaluate(`(async()=>{const c=await(await fetch('/api/intake/catalog/?engagement_id='+${JSON.stringify(catalogGroup)})).json();const s=await(await fetch('/api/session/csrf/')).json();return(await fetch('/api/intake/catalog/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Maipa-Client-Actor':c.actor_id},body:JSON.stringify({identity:c.identity,installation_id:c.installation_id,expected_revision:c.revision,action:'rename_engagement',engagement_id:${JSON.stringify(catalogGroup)},name:'Concurrent client name'})})).status;})()`);
    assert.equal(concurrentCatalog, 200);
    await fill(`catalog-name-client-${catalogGroup}`, 'Stale client name');
    await click('Save client name for Synthetic catalog client renamed');
    await until(() => evaluate("document.querySelector('[data-catalog-state]')?.dataset.catalogState === 'conflict'"), 'stale client setup refused');
    assert.equal(await evaluate("document.querySelectorAll('[data-catalog-revision]').length"), 0);
    assert.equal(await evaluate("!!document.getElementById('catalog-new-client')"), false);
    await click('Reload client setup'); await catalogReady(originalCatalog.revision+5);
    await select('catalog-client', catalogGroup); await catalogReady(originalCatalog.revision+5);
    catalog = await catalogState();
    assert.equal(catalog.engagements.find(row => row.id === catalogGroup).name, 'Concurrent client name');
    assert.equal(catalog.events.length, originalCatalog.events.length+5);
    assert.equal(await evaluate("document.querySelector('[data-pane=agent]')===window.maipaCatalogChat"), true);
    assert.deepEqual(await evaluate("(async()=>({preferences:await(await fetch('/api/workspace/preferences/')).json(),layout:await(await fetch('/api/workspace/layout/')).json(),setup:await(await fetch('/api/onboarding/')).json()}))()"), beforeCatalog);
    assert.deepEqual(await evaluate(`(async()=> (await fetch('/api/intake/entities/'+${JSON.stringify(saved.setup.entity_id)}+'/')).json())()`), saved.entity);
    record('client-catalog-create-rename-conflict');
    phase='workflow_editor';
    await requestComponent('Build a shared workflow for '+catalogEntity,'Maipa.Workflow');
    await until(()=>evaluate("document.querySelector('[data-workflow-phase]')?.dataset.workflowPhase==='ready'&&document.querySelectorAll('.workflow-workspace .react-flow__node').length===3"),'administrator shared workflow');
    await click('Publish version');
    await until(()=>evaluate("!([...document.querySelectorAll('button')].some(b=>b.textContent==='Publish version'))"),'administrator publishes exact graph');
    const permissionRuns=()=>evaluate("(async()=>{const id=document.querySelector('[data-workflow-draft]').dataset.workflowDraft;const s=await(await fetch('/api/session/')).json();const r=await fetch('/api/workflows/drafts/'+id+'/execution/',{headers:{'X-Maipa-Workflow-Actor':String(s.user.id)}});if(r.status!==200)throw Error('Owned execution read refused');return r.json();})()");
    await click('Agent permissions');
    await until(()=>evaluate("[...document.querySelectorAll('button')].some(b=>b.textContent==='Allow agent execution for up to one hour'&&!b.disabled)"),'human original-connection authorization available');
    await click('Allow agent execution for up to one hour');
    await until(()=>evaluate("document.querySelector('[data-workflow-agent-permission]')?.dataset.workflowAgentPermission==='enabled'"),'human permission persisted');
    assert.equal((await permissionRuns()).runs.length,0,'authorization creates no run');
    await typeChat('Run authorized shared workflow');await click('Send');await typeChat('Synthetic unsent permission follow-up');await waitForAgent('Maipa.Workflow');
    await until(async()=>{const s=await permissionRuns();return s.runs.length===1&&s.runs[0].status==='completed';},'human-authorized actual native worker execution');
    const permissionRun=(await permissionRuns()).runs[0];
    assert.deepEqual(Object.values(permissionRun.outputs).filter(v=>v?.greeting==='Hello ').length,2);
    assert.deepEqual(Object.values(permissionRun.nodes).map(n=>n.attempts),[1,1,1]);
    await until(()=>evaluate(`document.querySelector('[data-workflow-run]')?.dataset.workflowRun===${JSON.stringify(permissionRun.id)}&&document.querySelector('[data-workflow-run-status]')?.dataset.workflowRunStatus==='completed'`),'current completed native result selected in shared controls');
    await openRunDetails();
    assert.equal(await evaluate("[...document.querySelectorAll('.workflow-execution textarea')].some(t=>t.value.includes('Hello '))"),true);
    await click('▶ Run');
    await until(async()=>{const r=await permissionRuns();return r.runs.length===2&&r.runs.every(run=>run.status==='completed');},'human second run completed');
    const humanRun=(await permissionRuns()).runs.find(run=>run.id!==permissionRun.id);
    await typeChat('Select earlier shared workflow run');await click('Send');await typeChat('Synthetic unsent permission follow-up');await waitForAgent('Maipa.Workflow');
    await until(()=>evaluate(`document.querySelector('[data-workflow-run]')?.dataset.workflowRun===${JSON.stringify(permissionRun.id)}&&!!document.querySelector('[data-workflow-selection-source="agent"]')`),'agent selects actual earlier run in human UI');
    assert.equal(await evaluate("document.getElementById('maipa-chat-message').value"),'Synthetic unsent permission follow-up');
    const selector=await evaluate("document.querySelector('.workflow-execution select').id");
    await select(selector,humanRun.id);
    await until(()=>evaluate(`document.querySelector('[data-workflow-run]')?.dataset.workflowRun===${JSON.stringify(humanRun.id)}`),'human choice shared with agent');
    const chosen=(await permissionRuns()).selection;assert.equal(chosen.run_id,humanRun.id);assert.equal(chosen.source,'human');assert.equal(chosen.show_details,true);
    await command('Page.reload');await preferencesReady();
    await until(()=>evaluate(`document.querySelector('[data-workflow-run]')?.dataset.workflowRun===${JSON.stringify(humanRun.id)}&&[...document.querySelectorAll('.workflow-execution button')].some(b=>b.textContent==='Run details'&&b.getAttribute('aria-expanded')==='true')`),'same shared selection and details restored');
    assert.deepEqual((await permissionRuns()).selection,chosen);
    await click('Agent permissions');
    await until(()=>evaluate("[...document.querySelectorAll('button')].some(b=>b.textContent==='Revoke agent execution'&&!b.disabled)"),'current permission restored after reload');
    await click('Revoke agent execution');
    await until(()=>evaluate("document.querySelector('[data-workflow-agent-permission]')?.dataset.workflowAgentPermission==='disabled'"),'human revocation persisted');
    await typeChat('Try revoked shared workflow');await click('Send');await typeChat('Synthetic unsent permission follow-up');await waitForAgent('Maipa.Workflow');
    assert.equal((await permissionRuns()).runs.length,2,'revoked native request creates no run');
    assert.equal(await evaluate("document.getElementById('maipa-chat-message').value"),'Synthetic unsent permission follow-up');
    const permissionPreview=await command('Page.captureScreenshot',{format:'png'});
    writeFileSync('artifacts/ci/workflow-permissions.png',Buffer.from(permissionPreview.data,'base64'));
    record('shared-workflow-human-permissions-and-native-run');
    record('shared-workflow-run-selection-and-reload');
    await requestComponent('Open client facts', 'Maipa.ClientFacts');
    await until(() => evaluate(`document.querySelector('[data-entity-id]')?.dataset.entityId === ${JSON.stringify(saved.setup.entity_id)}`), 'original setup facts remain reachable');
    phase = 'admin_users';
    await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await until(() => evaluate("!document.querySelector('[data-pane=work]').inert"), 'desktop administrator controls');
    await preferencesReady();
    const beforeAdmin = await evaluate("(async()=>({preferences:await(await fetch('/api/workspace/preferences/')).json(),layout:await(await fetch('/api/workspace/layout/')).json(),setup:await(await fetch('/api/onboarding/')).json()}))()");
    await evaluate("window.maipaAdminChat=document.querySelector('[data-pane=agent]'); true");
    await requestComponent('Open users and access', 'Maipa.AdminUsers');
    const accessReady = revision => until(() => evaluate(`document.querySelector('[data-access-state]')?.dataset.accessState === 'ready' && ${revision === undefined ? 'true' : `document.querySelector('[data-access-revision]')?.dataset.accessRevision === '${revision}'`}`), 'current administrator state');
    await accessReady();
    await fill('admin-new-username', 'synthetic-access-user');
    await fill('admin-new-password', fixture.password);
    await fill('admin-new-confirmation', fixture.password);
    await select('admin-new-role', 'preparer');
    await click('Create user'); await accessReady(1);
    let access = await evaluate("(async()=> (await fetch('/api/admin/users/')).json())()");
    assert.equal(access.revision, 1);
    const added = access.users.find(user => user.username === 'synthetic-access-user');
    assert.equal(added.role, 'preparer'); assert.equal(added.enabled, true);
    assert.equal(access.events[0].subject_id, added.id);
    assert.equal(await evaluate("document.getElementById('admin-new-password').value"), '');
    await select(`admin-role-${added.id}`, 'viewer');
    await click('Save access for synthetic-access-user'); await accessReady(2);
    access = await evaluate("(async()=> (await fetch('/api/admin/users/')).json())()");
    assert.equal(access.revision, 2); assert.equal(access.users.find(user => user.id === added.id).role, 'viewer');
    const concurrentAccess = await evaluate(`(async()=>{const a=await(await fetch('/api/admin/users/')).json();const s=await(await fetch('/api/session/csrf/')).json();return (await fetch('/api/admin/users/'+${JSON.stringify(added.id)}+'/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Maipa-Admin':a.actor_id},body:JSON.stringify({expected_revision:a.revision,installation_id:a.installation_id,role:'reviewer',enabled:true})})).status;})()`);
    assert.equal(concurrentAccess, 200);
    await select(`admin-role-${added.id}`, 'preparer');
    await click('Save access for synthetic-access-user');
    await until(() => evaluate("document.querySelector('[data-access-state]')?.dataset.accessState === 'conflict'"), 'stale access form refused');
    assert.equal(await evaluate("document.querySelectorAll('[data-member-id]').length"), 0);
    await click('Reload users and access'); await accessReady(3);
    await select(`admin-role-${added.id}`, 'viewer'); await select(`admin-enabled-${added.id}`, 'false');
    await click('Save access for synthetic-access-user'); await accessReady(4);
    access = await evaluate("(async()=> (await fetch('/api/admin/users/')).json())()");
    assert.equal(access.revision, 4); assert.equal(access.users.find(user => user.id === added.id).enabled, false);
    await select(`admin-enabled-${added.id}`, 'true');
    await click('Save access for synthetic-access-user'); await accessReady(5);
    access = await evaluate("(async()=> (await fetch('/api/admin/users/')).json())()");
    assert.equal(access.revision, 5); assert.equal(access.events.length, 5);
    await select(`admin-role-${access.actor_id}`, 'viewer');
    await click(`Save access for ${fixture.username}`);
    await until(() => evaluate("document.querySelector('[data-access-state]')?.dataset.accessState === 'conflict'"), 'last administrator remains protected');
    assert.deepEqual(await evaluate("(async()=> (await fetch('/api/admin/users/')).json())()"), access);
    await click('Reload users and access'); await accessReady(5);
    assert.equal(await evaluate("document.querySelector('[data-pane=agent]')===window.maipaAdminChat"), true);
    assert.deepEqual(await evaluate("(async()=>({preferences:await(await fetch('/api/workspace/preferences/')).json(),layout:await(await fetch('/api/workspace/layout/')).json(),setup:await(await fetch('/api/onboarding/')).json()}))()"), beforeAdmin);
    record('admin-users-create-update-conflict');
    await click('Sign out');
    await until(() => evaluate("!!document.getElementById('maipa-username')"), 'claimed install uses sign-in');
    assert.equal(await evaluate("(async()=> (await fetch('/api/onboarding/')).status)()"), 401);
    assert.equal(await evaluate("!!document.getElementById('maipa-admin-username')"), false);
    record('first-run-stays-closed');
    phase = 'admin_users';
    if (await evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')")) await click('Got it');
    await fill('maipa-username', 'synthetic-access-user'); await fill('maipa-password', fixture.password);
    await click('Sign in');
    await until(() => evaluate("!!document.querySelector('.agent-workspace')"), 'created user can sign in');
    await until(() => evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')"), 'new-user release notes');
    await click('Got it');
    const restricted = await evaluate("(async()=>({role:(await(await fetch('/api/workspace/')).json()).role,admin:(await fetch('/api/admin/users/')).status,clients:await(await fetch('/api/intake/engagements/')).json()}))()");
    assert.equal(restricted.role, 'viewer'); assert.equal(restricted.admin, 403); assert.deepEqual(restricted.clients, { engagements: [] });
    assert.equal(await evaluate("[...document.querySelectorAll('.work-view-controls button')].some(button=>button.textContent==='Users and access')"), false);
    assert.equal(await evaluate("document.body.innerText.includes('Synthetic onboarding entity')"), false);
    record('admin-users-restricted-login');
    phase = 'client_catalog';
    const restrictedCatalog = await evaluate(`(async()=>({catalog:(await fetch('/api/intake/catalog/')).status,entity:(await fetch('/api/intake/entities/'+${JSON.stringify(catalogEntity)}+'/')).status,clients:await(await fetch('/api/intake/engagements/')).json()}))()`);
    assert.equal(restrictedCatalog.catalog, 403); assert.equal(restrictedCatalog.entity, 404);
    assert.deepEqual(restrictedCatalog.clients, { engagements: [] });
    assert.equal(await evaluate("[...document.querySelectorAll('.work-view-controls button')].some(button=>button.textContent==='Client setup')"), false);
    assert.equal(await evaluate("document.body.innerText.includes('Synthetic catalog') || document.body.innerText.includes('Concurrent client name')"), false);
    record('client-catalog-restricted-scope');
  } else {
  phase = "anonymous";
  await until(() => evaluate("!!document.getElementById('maipa-username')"), "anonymous workspace");
  assert.equal(await evaluate("document.querySelector('[data-maipa-build-id]')?.dataset.maipaBuildId"), fixture.build_id);
  const publicApi = await evaluate(`(async () => {
    const paths=['health','version','session','agent/accounts']; const values=[];
    for (const path of paths) { const r=await fetch('/api/'+path+'/', {cache:'no-store'}); values.push({status:r.status,body:await r.json(),cache:r.headers.get('cache-control')}); } return values; })()`);
  assert.deepEqual(publicApi.map(r => r.status), [200, 200, 401, 401]);
  assert.equal(publicApi[0].body.status, "ok");
  assert.equal(publicApi[1].body.build_id, fixture.build_id);
  assert.ok(publicApi.every(r => r.cache?.includes("no-store")));
  record("anonymous-authority-and-build");
  phase = "release_controls";
  await until(() => evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')"), "Fancy release notes");
  assert.ok(await evaluate(`document.body.innerText.includes(${JSON.stringify(fixture.release_label)})`));
  await click("Got it");
  await until(() => evaluate("!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')"), "release dismissal");
  await click("What's new");
  await until(() => evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')"), "reopen release notes");
  await click("Close updates");
  record("fancy-release-controls");
  phase = "theme";
  await click("Dark");
  await until(() => evaluate("document.querySelector('.appearance button[aria-pressed=true]')?.textContent === 'Dark'"), "dark preference");
  assert.equal(await evaluate("getComputedStyle(document.documentElement).getPropertyValue('--canvas').trim()"), "#111714");
  await command("Page.reload");
  await until(() => evaluate("document.querySelector('.appearance button[aria-pressed=true]')?.textContent === 'Dark'"), "persisted theme");
  record("theme-and-reload");
  phase = "login";
  checkpoint = 'login_viewport';
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });

  async function login(username) {
    if (phase === 'login') checkpoint = 'login_username';
    if (phase === 'logout_viewer') checkpoint = 'viewer_username';
    await fill("maipa-username", username);
    if (phase === 'login') checkpoint = 'login_password';
    if (phase === 'logout_viewer') checkpoint = 'viewer_password';
    await fill("maipa-password", fixture.password);
    if (phase === 'login') checkpoint = 'login_submit';
    if (phase === 'logout_viewer') checkpoint = 'viewer_submit';
    await click("Sign in");
    if (phase === 'login') checkpoint = 'login_workspace';
    if (phase === 'logout_viewer') checkpoint = 'viewer_workspace';
    await until(() => evaluate("!!document.querySelector('.agent-workspace')"), "real authenticated workspace");
    if (phase === 'login') checkpoint = 'login_notes';
    if (phase === 'logout_viewer') checkpoint = 'viewer_notes';
    if (await evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')")) await click("Got it");
  }
  checkpoint = 'login_wrong_username';
  await fill("maipa-username", fixture.username);
  checkpoint = 'login_wrong_password';
  await fill("maipa-password", "synthetic-incorrect-password");
  checkpoint = 'login_wrong_submit';
  await click("Sign in");
  checkpoint = 'login_wrong_response';
  await until(() => evaluate("document.querySelector('.auth-message')?.textContent.includes('could not sign you in')"), "generic failed login");
  checkpoint = 'login_password_cleared';
  assert.equal(await evaluate("document.getElementById('maipa-password').value"), "");
  checkpoint = 'login_reload';
  await command("Page.reload");
  checkpoint = 'login_form';
  await until(() => evaluate("!!document.getElementById('maipa-username')"), "login form");
  await login(fixture.username);
  checkpoint = 'login_scope';
  const workspace = await evaluate("(async()=>{const r=await fetch('/api/workspace/');return {status:r.status,body:await r.json()};})()");
  checkpoint = 'login_assertions';
  assert.equal(workspace.status, 200); assert.equal(workspace.body.role, "preparer");
  assert.ok(workspace.body.permissions.includes("agent.transport.check"));
  record("real-login-and-server-role");
  await checkChatViewport(897, 264);
  await checkChatViewport(390, 480);
  await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  phase = 'setup_isolation';
  const privateSetup = await evaluate("(async()=> (await fetch('/api/onboarding/')).json())()");
  assert.equal(privateSetup.step, 'display_name');
  assert.deepEqual(privateSetup.draft, {});
  assert.ok(!JSON.stringify(privateSetup).includes('Synthetic onboarding owner'));
  record('onboarding-user-isolation');
  phase = "authenticated_scope";
  const owned = await evaluate(`(async () => {const s=await (await fetch('/api/session/')).json(); const r=await fetch('/api/agent/accounts/'); return {session:s,accounts:await r.json()};})()`);
  assert.equal(owned.session.user.id, fixture.user_id);
  assert.ok(JSON.stringify(owned.accounts).includes(fixture.account_id));
  assert.ok(!JSON.stringify(owned.accounts).includes(fixture.foreign_account_id));
  record("authenticated-account-scope");
  phase = 'service_storage';
  assert.match(fixture.service_reference, /^[0-9a-f-]{36}$/);
  assert.match(fixture.artifact_reference, /^[0-9a-f-]{36}$/);
  const privateStorage = await evaluate(`(async()=>{
    const paths=['/api/agent/sessions/'+${JSON.stringify(fixture.service_reference)}+'/',
      '/artifacts/'+${JSON.stringify(fixture.artifact_reference.replaceAll('-', ''))}+'/payload',
      '/sessions/'+${JSON.stringify(fixture.service_reference)}+'/'];
    const results=[];for(const path of paths){const r=await fetch(path,{cache:'no-store'});results.push(r.status);}return results;
  })()`);
  assert.deepEqual(privateStorage, [404,404,404]);
  record('owned-service-storage-private');
  phase = 'ui_composition';
  assert.match(fixture.ui_lease, /^[0-9a-f-]{36}$/);
  const ui = await evaluate(`(async()=>{
    const path='/api/agent/accounts/'+${JSON.stringify(fixture.account_id)}+'/ui/'+${JSON.stringify(fixture.ui_lease)}+'/';
    const actor={'X-Maipa-Agent-Actor':${JSON.stringify(fixture.user_id)}};
    const csrf=(await(await fetch('/api/session/csrf/')).json()).csrf_token;
    const initial=await(await fetch(path,{headers:actor,cache:'no-store'})).json();
    const request=async(name,args,token=csrf)=>{const r=await fetch(path,{method:'POST',
      headers:{...actor,'Content-Type':'application/json',...(token?{'X-CSRFToken':token}:{})},
      body:JSON.stringify({method:'tools/call',params:{name,arguments:args}})});
      return {status:r.status,body:await r.json()};};
    const schema={id:'browser-note',type:'Maipa.Note',props:{text:'Synthetic contextual review'}};
    const denied=await request('maipa_ui_present',{revision:0,title:'Review',schema},'');
    const presented=await request('maipa_ui_present',{revision:0,title:'Review',schema});
    const stale=await request('maipa_ui_dismiss',{revision:0});
    const malicious=await request('maipa_ui_update',{revision:1,title:'Rejected',schema:{id:'unsafe',type:'iframe',props:{src:'https://foreign.invalid'}}});
    const preserved=await(await fetch(path,{headers:actor})).json();
    const dismissed=await request('maipa_ui_dismiss',{revision:1});
    const foreign=await fetch('/api/agent/accounts/'+${JSON.stringify(fixture.foreign_account_id)}+'/ui/'+${JSON.stringify(fixture.ui_lease)}+'/',{headers:actor});
    return {initial,denied:denied.status,presented,stale:stale.status,malicious:malicious.status,preserved,dismissed,foreign:foreign.status};
  })()`);
  assert.deepEqual(ui.initial, {id:fixture.ui_lease,revision:0,composition:null});
  assert.equal(ui.denied, 403);
  assert.equal(ui.presented.status, 200);
  assert.deepEqual(ui.presented.body, {id:fixture.ui_lease,revision:1,composition:{title:'Review',
    schema:{id:'browser-note',type:'Maipa.Note',props:{text:'Synthetic contextual review'}}}});
  assert.equal(ui.stale, 409); assert.equal(ui.malicious, 404); assert.equal(ui.foreign, 404);
  assert.deepEqual(ui.preserved, ui.presented.body);
  assert.deepEqual(ui.dismissed, {status:200,body:{id:fixture.ui_lease,revision:2,composition:null}});
  record('scoped-ui-composition-api');
  phase = "transport_csrf";
  const checks = await evaluate(`(async () => {
    const s=await (await fetch('/api/session/')).json(); const headers={'Content-Type':'application/json','X-CSRFToken':s.csrf_token};
    const base='/api/agent/accounts/'; const suffix='/transport-checks/';
    const foreign=await fetch(base+${JSON.stringify(fixture.foreign_account_id)}+suffix,{method:'POST',headers,body:'{}'});
    const denied=await fetch(base+${JSON.stringify(fixture.account_id)}+suffix,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    const own=await fetch(base+${JSON.stringify(fixture.account_id)}+suffix,{method:'POST',headers,body:'{}'});
    return {foreign:foreign.status,denied:denied.status,status:own.status,body:await own.json()};})()`);
  assert.equal(checks.foreign, 404); assert.equal(checks.denied, 403); assert.equal(checks.status, 201);
  assert.equal(checks.body.provider_ready, false);
  assert.ok(JSON.stringify(checks.body).includes("transport_verified"));
  record("csrf-and-real-zero-provider-transport");
  phase='work_views';
  await until(()=>evaluate("document.querySelectorAll('.header-shortcuts button').length===2"),'preparer common header shortcuts');
  assert.equal(await evaluate("!!document.querySelector('.header-shortcuts [aria-label=\"User management\"]')"),false);
  await until(()=>evaluate("document.querySelector('.chat-controls')?.dataset.draftState==='ready'"),'header draft ready');
  await typeChat('Unsent header navigation draft Ω');
  await evaluate("window.headerComposer=document.getElementById('maipa-chat-message');window.headerConversation=document.querySelector('.conversation');true");
  const headerBefore=await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return {chat:await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json(),views:await(await fetch('/api/workspace/views/')).json()};})()");
  assert.equal(headerBefore.chat.ui,null);assert.equal(headerBefore.chat.messages.length,0);
  await click('Agent settings');
  await until(()=>evaluate("document.querySelector('[data-agent-settings]')?.dataset.agentSettings==='ready'"),'offline header settings actual accounts');
  assert.equal(await evaluate("document.querySelector('.header-shortcuts [aria-label=\"Agent settings\"]').getAttribute('aria-pressed')"),'true');
  await click('Clients');
  await until(()=>evaluate("!!document.querySelector('[data-catalog-state=ready]')"),'offline header current client catalog');
  await click('Close contextual panel');
  await command('Emulation.setDeviceMetricsOverride',{width:390,height:480,deviceScaleFactor:1,mobile:true});
  assert.equal(await evaluate("(()=>{const b=document.querySelector('.header-shortcuts [aria-label=\"Agent settings\"]');b.focus();return document.activeElement===b;})()"),true);
  await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r',unmodifiedText:'\r'});
  await command('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await until(()=>evaluate("document.querySelector('.conversation').inert&&!document.querySelector('.contextual-panel').inert&&document.querySelector('[data-agent-settings]')?.dataset.agentSettings==='ready'"),'keyboard mobile header panel');
  await checkHeaderViewport();
  await click('Close contextual panel');
  await checkChatViewport(390,480);
  assert.equal(await evaluate("window.headerComposer===document.getElementById('maipa-chat-message')&&window.headerConversation===document.querySelector('.conversation')&&window.headerComposer.value==='Unsent header navigation draft Ω'"),true);
  const headerAfter=await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return {chat:await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json(),views:await(await fetch('/api/workspace/views/')).json()};})()");
  assert.deepEqual(headerAfter,headerBefore);
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  record('common-header-shortcuts');

  await requestComponent('Open client facts', 'Maipa.ClientFacts');
  phase = "keyboard_divider";
  const divider = await evaluate("(() => {const d=document.querySelector('[role=separator]'); d.focus(); return +d.getAttribute('aria-valuenow');})()");
  await command("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 });
  await command("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 });
  await until(async () => (await evaluate("+document.querySelector('[role=separator]').getAttribute('aria-valuenow')")) > divider, "keyboard divider");
  record("keyboard-divider");
  await until(() => evaluate("document.querySelector('[data-layout-state]')?.dataset.layoutState === 'ready'"), 'keyboard layout saved');
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'ArrowLeft', windowsVirtualKeyCode: 37 });
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'ArrowLeft', windowsVirtualKeyCode: 37 });
  await until(() => evaluate("document.querySelector('[data-layout-state]')?.dataset.layoutState === 'ready' && +document.querySelector('[role=separator]').getAttribute('aria-valuenow') === 60"), 'keyboard layout restored');
  phase = "mobile_panels";
  await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await until(() => evaluate("!document.querySelector('[data-pane=agent]').inert && document.querySelector('[data-pane=work]').inert"), "mobile work area");
  await click("Shared work");
  await until(() => evaluate("document.querySelector('[data-pane=agent]').inert && !document.querySelector('[data-pane=work]').inert"), "mobile contextual panel");
  record("mobile-panels");
  await click('Conversation');
  await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  phase = "intake_recovery";
  await until(() => intakeListResponses.includes(503), "deliberate first intake read failure");
  await until(() => evaluate("document.querySelector('.intake-card [role=alert]')?.textContent.includes('could not confirm')"), "honest initial-list error");
  await click("Reload facts");
  await until(() => intakeListResponses.includes(200), "actual Django engagement-list retry");
  await until(() => evaluate("!document.querySelector('.intake-card [role=alert]')"), "recovered initial list");
  await until(() => evaluate("!!document.getElementById('intake-engagement-name')"), "new engagement form");
  await fill("intake-engagement-name", "Synthetic browser engagement");
  await fill("intake-entity-name", "Synthetic browser entity");
  await click("Create engagement");
  await until(() => evaluate("document.querySelector('[data-entity-revision]')?.dataset.entityRevision === '0'"), "created entity");
  const clientEntityId = await evaluate("document.querySelector('[data-entity-id]').dataset.entityId");
  assert.match(clientEntityId, /^[0-9a-f-]{36}$/);
  const secondEntity = await evaluate(`(async()=>{
    const s=await(await fetch('/api/session/')).json();
    const entity=await(await fetch('/api/intake/entities/'+${JSON.stringify(clientEntityId)}+'/')).json();
    const r=await fetch('/api/intake/engagements/'+entity.engagement_id+'/entities/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token},body:JSON.stringify({name:'Synthetic second browser entity'})});
    return {status:r.status,body:await r.json()};})()`);
  assert.equal(secondEntity.status, 201);
  await click("Reload facts");
  await until(() => evaluate("document.querySelector('.intake-card').getAttribute('aria-busy') === 'false' && document.getElementById('intake-entity').options.length === 2"), "external entity appears on list reload");
  await select("intake-entity", secondEntity.body.id);
  await until(() => evaluate(`document.querySelector('[data-entity-id]')?.dataset.entityId === ${JSON.stringify(secondEntity.body.id)}`), "second selected entity");
  const responsesBefore = intakeListResponses.length;
  await click("Reload facts");
  await until(() => intakeListResponses.length > responsesBefore, "actual selected-entity list refresh");
  await until(() => evaluate(`document.querySelector('.intake-card').getAttribute('aria-busy') === 'false' && document.getElementById('intake-entity').value === ${JSON.stringify(secondEntity.body.id)} && document.querySelector('[data-entity-id]')?.dataset.entityId === ${JSON.stringify(secondEntity.body.id)}`), "list reload retains selected entity");
  await select("intake-entity", clientEntityId);
  await until(() => evaluate(`document.querySelector('[data-entity-id]')?.dataset.entityId === ${JSON.stringify(clientEntityId)}`), "first entity remains separately available");
  record("intake-initial-load-recovery");
  phase = 'work_views';
  await until(() => evaluate("!!document.getElementById('maipa-setup-answer') && document.querySelector('[data-setup-step]').getAttribute('aria-busy') === 'false'"), 'available saved conversation');
  await fill('maipa-setup-answer', 'Synthetic unsent setup draft');
  await evaluate("window.maipaFixtureChat = document.querySelector('[aria-label=\"Account onboarding\"]'); window.maipaFixtureComposer = document.getElementById('maipa-setup-answer'); true");
  await requestComponent('Present a note', 'Maipa.Note');
  await until(() => evaluate("!!document.querySelector('.agent-commentary')"), 'published guide view');
  await click('Save view');
  await until(()=>evaluate("document.querySelector('button[aria-label=\"Open saved view Shared work\"]')?.getAttribute('aria-pressed')==='true'"),'acknowledged personal pin');
  const firstPin=await evaluate("(async()=>{const s=await(await fetch('/api/workspace/views/')).json();return s.views[0].id;})()");
  await typeChat('Unsent while closing and reopening Ω');
  await evaluate("window.maipaPinDraft=document.querySelector('#maipa-chat-message');true");
  await click('Close contextual panel');
  await until(()=>evaluate("!document.querySelector('.has-composition')"),'closed work returns space to chat');
  await click('Open saved view Shared work');
  await until(()=>evaluate("document.querySelector('button[aria-label=\"Open saved view Shared work\"]')?.getAttribute('aria-pressed')==='true'&&document.querySelector('.saved-view-bar')?.dataset.viewsState==='ready'"),'personal view reopened and acknowledged');
  assert.equal(await evaluate("window.maipaPinDraft===document.querySelector('#maipa-chat-message')&&window.maipaPinDraft.value==='Unsent while closing and reopening Ω'"),true);
  await manageSavedViews();await fill('maipa-view-title','Pinned review');await click('Save name');
  await until(()=>evaluate("document.querySelector('.contextual-panel h2')?.textContent==='Pinned review'"),'atomic human rename shared with native UI');
  await click('Close saved views');
  await requestComponent('Save a personal view', 'Maipa.Note');
  await until(()=>evaluate("!!document.querySelector('button[aria-label=\"Open saved view Agent saved review\"]')"),'native agent saved second pin');
  await requestComponent('Update my first saved view', 'Maipa.Note');
  await until(()=>evaluate("document.querySelector('.agent-commentary')?.textContent.includes('Synthetic agent pin update')"),'native agent updates saved human definition');
  let pinState=await evaluate("(async()=>await(await fetch('/api/workspace/views/')).json())()");
  assert.equal(pinState.views[0].id,firstPin);assert.equal(pinState.views[0].schema.id,'shared-root');
  assert.equal(pinState.views[0].revision,3);assert.deepEqual(pinState.views[0].history.map(v=>v.title),['Shared work','Pinned review']);
  await until(()=>evaluate(`Number(document.querySelector('[data-views-revision]')?.dataset.viewsRevision)===${pinState.revision}`),'current native collection revision acknowledged in human controls');
  await manageSavedViews();await select('maipa-saved-view',firstPin);await click('Move saved view later');
  await until(()=>evaluate(`(async()=>{const s=await(await fetch('/api/workspace/views/')).json();return s.views[1].id===${JSON.stringify(firstPin)};})()`),'saved pin order acknowledged');
  await click('Unpin');
  await until(()=>evaluate("!document.querySelector('button[aria-label=\"Open saved view Pinned review updated\"]')"),'unpin removes shortcut without deleting definition');
  await click('Close saved views');
  pinState=await evaluate("(async()=>await(await fetch('/api/workspace/views/')).json())()");
  assert.equal(pinState.views.length,2);assert.equal(pinState.views[1].id,firstPin);assert.equal(pinState.views[1].pinned,false);
  await checkChatViewport(897,264);
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await requestComponent('Compose client overview', 'Maipa.Stack');
  await until(()=>evaluate("!!document.querySelector('.client-table [data-react-fancy-table]') && !!document.querySelector('button[aria-label=\"Open saved view Current client overview\"]')"),'finite native peer composes and pins current client table');
  assert.equal(await evaluate("!!document.querySelector('[data-react-fancy-heading]') && !!document.querySelector('[data-react-fancy-callout]')"),true);
  assert.equal(await evaluate("document.querySelector('.composition-callout strong')?.textContent"),'current');
  assert.deepEqual(await evaluate("[...document.querySelectorAll('.client-table th')].map(n=>n.textContent)"),['Entities','Client']);
  assert.equal(await evaluate("(async()=>{const current=await(await fetch('/api/intake/catalog/')).json();return current.engagements.every(row=>document.querySelector('[data-client-id=\"'+row.id+'\"] [data-client-column=name]')?.textContent===row.name&&document.querySelector('[data-client-id=\"'+row.id+'\"] [data-client-column=entity_count]')?.textContent===String(row.entity_count));})()"),true);
  await command('Fetch.enable',{patterns:[{urlPattern:fixture.origin+'/api/intake/catalog/',requestStage:'Request'}]});
  failClientTableRead=true;
  await click('Reload client table');
  await until(()=>evaluate("document.querySelector('.client-table [role=alert]')?.textContent.includes('could not confirm') && !document.querySelector('.client-table [data-client-id]')"),'failed refresh clears old client rows without breaking chat');
  await command('Fetch.disable');
  await click('Reload client table');
  await until(()=>evaluate("!!document.querySelector('.client-table [data-client-id]') && !document.querySelector('.client-table [role=alert]')"),'actual current client service retry');
  const paletteScreenshot=await command('Page.captureScreenshot',{format:'png'});
  writeFileSync('artifacts/ci/component-palette.png',Buffer.from(paletteScreenshot.data,'base64'));
  await checkChatViewport(897,264);
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  assert.equal(await evaluate("document.querySelector('[aria-label=\"Account onboarding\"]') === window.maipaFixtureChat && document.getElementById('maipa-setup-answer') === window.maipaFixtureComposer && window.maipaFixtureComposer.value === 'Synthetic unsent setup draft'"), true);
  await requestComponent('Open client facts', 'Maipa.ClientFacts');
  await until(() => evaluate(`document.querySelector('[data-entity-id]')?.dataset.entityId === ${JSON.stringify(clientEntityId)}`), 'facts remain reachable as a view');
  await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await click('Conversation');
  await until(() => evaluate("!document.querySelector('[data-pane=agent]').inert"), 'mobile conversation remains mounted');
  await click('Shared work');
  await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await until(() => evaluate("innerWidth===1440 && !document.querySelector('[data-pane=work]').inert && !document.querySelector('[data-pane=agent]').inert"), 'desktop view restored');
  assert.equal(await evaluate("document.getElementById('maipa-setup-answer') === window.maipaFixtureComposer && window.maipaFixtureComposer.value === 'Synthetic unsent setup draft'"), true);
  record('persistent-chat-work-views');
  phase = 'agent_shell';
  assert.equal(await evaluate("!!document.querySelector('[data-react-fancy-prompt-input]')"), true, 'main conversation uses the public whole PromptInput');
  assert.equal(await evaluate("!!document.querySelector('.maipa-chat-drawer') && !!document.querySelector('[data-react-fancy-virtual-list]')"), true, 'kit drawer and measured transcript are mounted');
  await click('Requests'); await click('Ask for a note');
  await typeChat(Array(12).fill('Synthetic multiline draft').join('\n'));
  await checkChatViewport(897, 480);
  await checkChatViewport(600, 480);
  await checkChatViewport(390, 600);
  await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await click('Ask for a note');
  assert.equal(await evaluate("document.getElementById('maipa-chat-message').value"), 'Present a note');
  const drawerToggle = await evaluate("document.querySelector('.maipa-chat-drawer button[aria-label]')?.getAttribute('aria-label')");
  if (drawerToggle) await click(drawerToggle);
  await typeChat('Draft before composition');
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', windowsVirtualKeyCode: 229, modifiers: 2 });
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', windowsVirtualKeyCode: 229, modifiers: 2 });
  assert.equal(await evaluate("document.getElementById('maipa-chat-message').value === 'Draft before composition' && !document.querySelector('[data-submit-state=submitting]')"), true);
  const syntheticAttachment = join(profile, 'synthetic-evidence.txt'); writeFileSync(syntheticAttachment, 'Synthetic unsent evidence');
  const docRoot = await command('DOM.getDocument', { depth: 0 });
  const attachmentInput = await command('DOM.querySelector', { nodeId: docRoot.root.nodeId, selector: '.chat-composer input[type=file]' });
  await command('DOM.setFileInputFiles', { nodeId: attachmentInput.nodeId, files: [syntheticAttachment] });
  await until(() => evaluate("!!document.querySelector('[data-react-fancy-prompt-input-attachment]')"), 'real selected attachment appears');
  await click('Send');
  await until(() => evaluate("document.querySelector('.chat-controls [role=alert]')?.textContent.includes('Remove them')"), 'unsupported delivery is explicitly refused');
  assert.equal(await evaluate("document.getElementById('maipa-chat-message').value==='Draft before composition' && !!document.querySelector('[data-react-fancy-prompt-input-attachment]')"), true);
  await until(()=>evaluate("document.querySelector('[data-draft-state]')?.dataset.draftState==='ready'"),'file metadata draft saved');
  await command('Page.reload');await preferencesReady();
  await until(()=>evaluate("document.querySelector('[data-draft-state]')?.dataset.draftState==='ready'&&document.getElementById('maipa-chat-message')?.value==='Draft before composition'&&!!document.querySelector('[data-react-fancy-prompt-input-attachment]')"),'file name and full draft restored without invented bytes');
  await click('Send');
  await until(()=>evaluate("!!document.querySelector('[data-submit-state=error]')"),'unrestored attachment cannot silently disappear into a send');
  await click('Remove attachment');
  await command('Fetch.enable', { patterns: [{ urlPattern: fixture.origin + '/api/agent/chat/', requestStage: 'Request' }] });
  holdComposerSends = true;
  await typeChat('Check acknowledged send');
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', windowsVirtualKeyCode: 13, modifiers: 2 });
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', windowsVirtualKeyCode: 13, modifiers: 2 });
  await until(() => pausedComposerSends.length === 1, 'actual send request is held before acknowledgment');
  assert.equal(await evaluate("!!document.querySelector('[data-submit-state=submitting]') && !document.getElementById('maipa-chat-message').disabled && document.getElementById('maipa-chat-message').value==='Check acknowledged send'"), true);
  await typeChat('New typing while pending Ω');
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', windowsVirtualKeyCode: 13, modifiers: 2 });
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', windowsVirtualKeyCode: 13, modifiers: 2 });
  await delay(100); assert.equal(pausedComposerSends.length, 1, 'no duplicate submission while pending');
  await command('Fetch.failRequest', { requestId: pausedComposerSends[0].requestId, errorReason: 'Failed' });
  await until(() => evaluate("!!document.querySelector('[data-submit-state=error]')"), 'actual failed request preserves draft');
  assert.equal(await evaluate("document.getElementById('maipa-chat-message').value"), 'New typing while pending Ω');
  await typeChat('Check acknowledged send');
  await until(()=>evaluate("document.querySelector('[data-draft-state]')?.dataset.draftState==='ready'"),'unconfirmed retry and draft saved');
  await command('Fetch.disable');await command('Page.reload');await preferencesReady();
  await until(()=>evaluate("document.querySelector('[data-draft-state]')?.dataset.draftState==='ready'&&document.getElementById('maipa-chat-message')?.value==='Check acknowledged send'"),'exact failed-send draft restored');
  await command('Fetch.enable', { patterns: [{ urlPattern: fixture.origin + '/api/agent/chat/', requestStage: 'Request' }] });
  await click('Send');
  await until(() => pausedComposerSends.length === 2, 'same message retry reaches transport');
  assert.equal(pausedComposerSends[1].body.request_id, pausedComposerSends[0].body.request_id, 'retry retains original idempotency identity');
  await typeChat('New typing after retry Ω'); holdComposerSends = false;
  await command('Fetch.continueRequest', { requestId: pausedComposerSends[1].requestId });
  await command('Fetch.disable'); await waitForAgent('Maipa.Note');
  assert.equal(await evaluate("document.getElementById('maipa-chat-message').value"), 'New typing after retry Ω');
  await command('Fetch.enable', { patterns: [{ urlPattern: fixture.origin + '/api/agent/chat/', requestStage: 'Request' }] });
  holdComposerSends = true;
  await typeChat('Keep my edited and restored draft'); await click('Send');
  await until(() => pausedComposerSends.length === 3, 'revision-check send is held before acknowledgment');
  await typeChat('Keep my edited and restored draft with a newer edit');
  await typeChat('Keep my edited and restored draft');
  holdComposerSends = false;
  await command('Fetch.continueRequest', { requestId: pausedComposerSends[2].requestId });
  await command('Fetch.disable'); await waitForAgent('Maipa.Note');
  assert.equal(await evaluate("document.getElementById('maipa-chat-message').value"), 'Keep my edited and restored draft', 'a newer edit restored to identical text survives acknowledgment');
  if (await evaluate("document.querySelector('.account-menu')?.open")) await click('Account and appearance');
  await typeChat('Present a note with a delayed update'); await click('Send');
  await until(() => evaluate("document.querySelector('.agent-commentary p')?.textContent==='Synthetic shared note'"), 'agent first presentation');
  await evaluate("window.maipaDraftNode=document.querySelector('#maipa-chat-message');window.maipaConversationNode=document.querySelector('.conversation');true");
  await typeChat('Unsent follow-up Ω');
  await until(() => evaluate("document.querySelector('.agent-commentary p')?.textContent==='Synthetic delayed update' && document.querySelector('.chat-status [role=status]')?.textContent==='Ready'"), 'agent updates existing shared component');
  assert.equal(await evaluate("document.querySelector('#maipa-chat-message')===window.maipaDraftNode && window.maipaDraftNode.value==='Unsent follow-up Ω' && document.querySelector('.conversation')===window.maipaConversationNode"), true);
  await until(() => evaluate("document.getElementById('maipa-agent-model')?.value==='synthetic-a' && !document.getElementById('maipa-agent-model').disabled"), 'provider-reported model choices');
  await evaluate("window.maipaModelPanelNode=document.querySelector('.contextual-panel');true");
  await select('maipa-agent-model', 'synthetic-b');
  await until(() => evaluate("document.getElementById('maipa-agent-model')?.value==='synthetic-b' && !document.getElementById('maipa-agent-model').disabled && document.querySelector('.chat-status [role=status]')?.textContent==='Ready'"), 'confirmed owned model choice');
  assert.equal(await evaluate("window.maipaDraftNode===document.querySelector('#maipa-chat-message') && window.maipaDraftNode.value==='Unsent follow-up Ω' && window.maipaModelPanelNode===document.querySelector('.contextual-panel')"), true);
  await typeChat('Check kit formatting and literal <user-text> **raw**'); await click('Send');
  await until(() => evaluate("document.querySelector('.chat-status [role=status]')?.textContent==='Ready' && [...document.querySelectorAll('.chat-message.assistant')].at(-1)?.textContent.includes('Formatted reply')"), 'kit renderer reply completed');
  assert.equal(await evaluate("[...document.querySelectorAll('.chat-message.assistant')].at(-1)?.querySelector('[data-react-fancy-content-renderer] strong')?.textContent"), 'Formatted reply');
  assert.equal(await evaluate("[...document.querySelectorAll('.chat-message.assistant')].at(-1)?.querySelector('[data-react-fancy-content-renderer] h2')?.textContent"), 'Kit reply');
  assert.equal(await evaluate("[...document.querySelectorAll('.chat-message.assistant')].at(-1)?.querySelector('[data-react-fancy-content-renderer] li')?.textContent"), 'One item');
  assert.equal(await evaluate("document.querySelector('.agent-commentary [data-react-fancy-content-renderer] strong')?.textContent"), 'Kit note');
  assert.equal(await evaluate("document.querySelector('.chat-message.user:last-of-type')?.textContent.includes('literal <user-text> **raw**') || [...document.querySelectorAll('.chat-message.user')].at(-1)?.textContent.includes('literal <user-text> **raw**')"), true);
  assert.equal(await evaluate("document.querySelector('#maipa-chat-message').hasAttribute('data-react-fancy-prompt-input-textarea')"), true);
  assert.equal(await evaluate("!!window.maipaUnsafeRender || !!document.querySelector('.chat-message.assistant img, .chat-message.assistant iframe, .chat-message.assistant script, .chat-message.assistant [onerror], .agent-commentary img, .agent-commentary script')"), false);
  assert.equal(await evaluate("[...document.querySelectorAll('.chat-message.assistant')].at(-1)?.querySelector('a')?.hasAttribute('href')"), false);
  const scrollPoint = await evaluate("(()=>{const r=document.querySelector('[data-react-fancy-virtual-list]').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()");
  await command('Input.dispatchMouseEvent', { type: 'mouseWheel', ...scrollPoint, deltaX: 0, deltaY: -1200 });
  await until(() => evaluate("document.querySelector('[data-react-fancy-virtual-list]').getAttribute('data-react-fancy-virtual-list-following')==='false'"), 'human scrolling pauses automatic following');
  // Measured message spacing makes long histories taller than one wheel gesture.
  const historyHeight=await evaluate("document.querySelector('[data-react-fancy-virtual-list]').scrollHeight");
  await command('Input.dispatchMouseEvent', { type: 'mouseWheel', ...scrollPoint, deltaX: 0, deltaY: -historyHeight });
  await delay(250);
  assert.equal(await evaluate("document.querySelector('[data-react-fancy-virtual-list]').scrollTop < 5"), true);
  await checkChatViewport(1200,800);
  assert.equal(await evaluate("document.querySelector('[data-react-fancy-virtual-list]').getAttribute('data-react-fancy-virtual-list-following')==='false' && document.querySelector('[data-react-fancy-virtual-list]').scrollTop < 5"),true,'Resizing while reading history must preserve paused following');
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await click('Back to latest');
  await until(() => evaluate("document.querySelector('[data-react-fancy-virtual-list]').getAttribute('data-react-fancy-virtual-list-following')==='true'"), 'person resumes following explicitly');
  await checkTranscriptTail('Back to latest');
  await checkChatViewport(903,527);
  await checkTranscriptTail('short-window resize');
  await typeChat('Unsent follow-up Ω');
  record('agent-chat-stream-and-ui-control');
  await click('Close contextual panel');
  await until(() => evaluate("!!document.querySelector('.chat-only')"), 'human dismissal reaches shared composition');
  assert.equal(await evaluate("window.maipaDraftNode===document.querySelector('#maipa-chat-message') && window.maipaDraftNode.value==='Unsent follow-up Ω'"), true);
  assert.equal(await evaluate("document.querySelector('.conversation').getBoundingClientRect().width >= innerWidth-5 && getComputedStyle(document.querySelector('[role=separator]')).display==='none'"), true);
  await checkTranscriptTail('contextual-panel dismissal and message reflow');
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  await checkTranscriptTail('expanded viewport');
  for (const [width,height] of [[903,527],[1440,1000],[903,527],[1440,1000]]) {
    await command('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
    await checkTranscriptTail('repeated resize '+width+'x'+height);
  }
  record('chat-draft-and-space-recovery');
  await typeChat('Present a note and wait for cancellation'); await click('Send');
  await until(() => evaluate("document.querySelector('.chat-status [role=status]')?.textContent==='Maipa is working…' && [...document.querySelectorAll('.chat-message.assistant')].at(-1)?.querySelector('p')?.textContent==='Synthetic agent '"), 'streaming partial reply');
  await checkTranscriptTail('streaming partial reply');
  await click('Stop');
  await until(() => evaluate("document.querySelector('.chat-status [role=status]')?.textContent==='Ready' && [...document.querySelectorAll('.chat-message.assistant')].at(-1)?.querySelector('small')?.textContent.includes('Interrupted')"), 'cancelled partial reply retained');
  const savedChat = await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json()).messages;})()");
  await typeChat('Durable unsent follow-up Ω');
  await until(()=>evaluate("document.querySelector('[data-draft-state]')?.dataset.draftState==='ready'"),'unsent draft saved before reload');
  await command('Page.reload'); await preferencesReady();
  await until(() => evaluate("document.querySelector('.chat-status [role=status]')?.textContent==='Ready'"), 'live chat restored after reload');
  assert.deepEqual(await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json()).messages;})()"), savedChat);
  assert.equal(await evaluate("[...document.querySelectorAll('.chat-message.assistant')].at(-1)?.querySelector('small')?.textContent.includes('Interrupted')"), true);
  record('agent-cancel-and-chat-restore');
  await until(()=>evaluate("document.getElementById('maipa-chat-message')?.value==='Durable unsent follow-up Ω'&&document.querySelector('[data-draft-state]')?.dataset.draftState==='ready'"),'unsent draft restored without resubmitting saved transcript');
  record('agent-draft-reload-and-retry');
  await typeChat('Simulate connection interruption'); await click('Send');
  await until(() => evaluate("document.querySelector('.chat-controls [role=alert]')?.textContent.includes('disconnected')"), 'deliberate owned transport interruption');
  const interruptedChat = await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json()).messages;})()");
  await typeChat('Unsent reconnect draft');
  await click('Reconnect');
  await until(() => evaluate("document.querySelector('.chat-status [role=status]')?.textContent==='Ready'"), 'explicit reconnect restarts stopped worker');
  assert.equal(await evaluate("document.querySelector('#maipa-chat-message').value"), 'Unsent reconnect draft');
  assert.deepEqual(await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json()).messages;})()"), interruptedChat);
  await requestComponent('Present a note after reconnect', 'Maipa.Note');
  assert.equal(await evaluate("!!document.querySelector('.chat-controls [role=alert]')"), false);
  record('agent-reconnect-after-transport-loss');
  await typeChat('Codex unsent draft');
  const beforeSwitch = await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json();})()");
  assert.equal(await evaluate("document.getElementById('maipa-agent-provider').disabled"), true);
  await click('Disconnect');
  await until(() => evaluate("!document.getElementById('maipa-agent-provider').disabled"), 'verified stopped connection allows a provider change');
  await select('maipa-agent-provider', 'independent-acp');
  await until(() => evaluate("document.getElementById('maipa-agent-provider').value==='independent-acp' && document.querySelector('.chat-status [role=status]').textContent.includes('not available')"), 'unsupported provider remains explicit');
  assert.equal(await evaluate("document.querySelectorAll('.chat-message').length===0 && ![...document.querySelectorAll('.chat-status button')].some(b=>b.textContent.startsWith('Connect '))"), true);
  await typeChat('Independent ACP unsent draft');
  const providerPreview = await command('Page.captureScreenshot', { format: 'png' });
  writeFileSync('artifacts/ci/agent-provider-preview.png', Buffer.from(providerPreview.data, 'base64'));
  await select('maipa-agent-provider', 'codex');
  await until(() => evaluate("document.getElementById('maipa-agent-provider').value==='codex' && document.querySelector('#maipa-chat-message').value==='Codex unsent draft'"), 'original provider draft restored');
  assert.deepEqual(await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json()).messages;})()"), beforeSwitch.messages);
  await select('maipa-agent-provider', 'independent-acp');
  await until(() => evaluate("document.getElementById('maipa-agent-provider').value==='independent-acp' && document.querySelector('#maipa-chat-message').value==='Independent ACP unsent draft'"), 'other provider draft remains separate');
  await command('Page.reload'); await preferencesReady();
  await until(() => evaluate("document.getElementById('maipa-agent-provider')?.value==='independent-acp' && document.querySelector('.chat-status [role=status]').textContent.includes('not available')"), 'provider selection survives reload without fallback');
  await select('maipa-agent-provider', 'claude');
  await until(() => evaluate("[...document.querySelectorAll('button')].some(b=>b.textContent==='Connect Claude'&&!b.disabled)"), 'Claude adapter exposes its own connection');
  await typeChat('Claude unsent draft');
  await click('Connect Claude');
  await until(() => evaluate("!!document.querySelector('.provider-signin')"), 'owned Claude sign-in');
  await click('Sign in with Claude');
  await until(() => evaluate("!!document.getElementById('maipa-provider-authorization')"), 'private provider callback control');
  assert.equal(await evaluate("document.querySelector('.provider-signin a')?.href==='https://claude.com/cai/oauth/authorize?state=synthetic' && document.getElementById('maipa-provider-authorization').type==='password'"), true);
  await fill('maipa-provider-authorization', 'SYNTHETIC_PRIVATE_CODE');
  await click('Complete sign-in');
  await until(() => evaluate("document.querySelector('.chat-status [role=status]')?.textContent==='Ready'"), 'private callback completes deterministic connection');
  assert.equal(await evaluate("!document.querySelector('.provider-signin') && document.querySelectorAll('.chat-message').length===0 && document.querySelector('#maipa-chat-message').value==='Claude unsent draft' && !document.body.textContent.includes('SYNTHETIC_PRIVATE_CODE')"), true);
  await click('Disconnect');
  await until(() => evaluate("!document.getElementById('maipa-agent-provider').disabled"), 'Claude worker stopped before switch');
  await select('maipa-agent-provider', 'codex');
  await until(() => evaluate("document.getElementById('maipa-agent-provider').value==='codex' && !document.getElementById('maipa-agent-provider').disabled"), 'original provider selected again');
  await requestComponent('Present a note after provider change', 'Maipa.Note');
  record('agent-provider-choice-and-account-restore');
  await requestComponent('Open agent settings', 'Maipa.AgentSettings');
  await until(()=>evaluate("document.querySelector('[data-agent-settings]')?.dataset.agentSettings==='ready'&&!!document.querySelector('#settings-model option[value=synthetic-b]')"),'owned settings and actual model choices');
  const settingsBefore=await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':String(s.user.id),'X-Maipa-Agent-Models':'1'}})).json();})()");
  const settingsNext=settingsBefore.models.currentModelId==='synthetic-a'?'synthetic-b':'synthetic-a';
  await typeChat('Unsent settings follow-up Ω');
  await evaluate("window.maipaSettingsComposer=document.querySelector('#maipa-chat-message');true");
  await select('settings-model',settingsNext);
  await until(()=>evaluate(`document.getElementById('settings-model')?.value===${JSON.stringify(settingsNext)}&&document.getElementById('maipa-agent-model')?.value===${JSON.stringify(settingsNext)}&&!document.getElementById('settings-model').disabled`),'acknowledged model shared with persistent chat');
  assert.equal(await evaluate("window.maipaSettingsComposer===document.querySelector('#maipa-chat-message')&&document.getElementById('maipa-chat-message').value==='Unsent settings follow-up Ω'"),true);
  const settingsAfter=await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':String(s.user.id),'X-Maipa-Agent-Models':'1'}})).json();})()");
  assert.equal(settingsAfter.models.currentModelId,settingsNext);
  for(const key of ['id','messages','ui','connection'])assert.deepEqual(settingsAfter[key],settingsBefore[key]);
  await until(()=>evaluate("document.querySelector('[data-draft-state]')?.dataset.draftState==='ready'"),'unsent settings draft acknowledged');
  await command('Page.reload');await preferencesReady();
  await until(()=>evaluate(`document.querySelector('[data-agent-settings]')?.dataset.agentSettings==='ready'&&document.getElementById('settings-model')?.value===${JSON.stringify(settingsNext)}&&document.getElementById('maipa-chat-message')?.value==='Unsent settings follow-up Ω'`),'same connection model and unsent draft after reload');

  const commonSharedBefore=await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json());})()");
  await click('Clients');
  await until(()=>evaluate("!!document.querySelector('[data-catalog-state=ready]')"),'human header replaces same agent panel');
  const commonSharedAfter=await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json());})()");
  assert.equal(commonSharedAfter.ui.id,commonSharedBefore.ui.id);assert.equal(commonSharedAfter.ui.revision,commonSharedBefore.ui.revision+1);
  assert.equal(commonSharedAfter.ui.composition.schema.type,'Maipa.ClientCatalog');assert.deepEqual(commonSharedAfter.messages,commonSharedBefore.messages);
  await click('Agent settings');await until(()=>evaluate("document.querySelector('[data-agent-settings]')?.dataset.agentSettings==='ready'"),'header returns to same shared settings');
  record('agent-settings-and-actual-model-controls');
  await until(()=>evaluate("document.querySelector('[data-account-metadata]')?.dataset.accountMetadata==='ready'"),'original account display metadata loaded');
  const metadataPath='/api/agent/accounts/'+settingsAfter.connection.account_id+'/metadata/';
  const readMetadata=()=>evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();const r=await fetch(${JSON.stringify(metadataPath)},{cache:'no-store',headers:{'X-Maipa-Agent-Actor':String(s.user.id)}});if(r.status!==200)throw Error('Owned metadata read refused');return r.json();})()`);
  const metadataBefore=await readMetadata();
  const originalMetadataChat=await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':String(s.user.id),'X-Maipa-Agent-Models':'1'}})).json());})()");
  await evaluate("window.maipaMetadataComposer=document.getElementById('maipa-chat-message');true");
  await fill('settings-account-name','Scoped account Ω');
  await command('Input.dispatchKeyEvent',{type:'keyDown',key:'Enter',code:'Enter',windowsVirtualKeyCode:13,text:'\r',unmodifiedText:'\r'});
  await command('Input.dispatchKeyEvent',{type:'keyUp',key:'Enter',code:'Enter',windowsVirtualKeyCode:13});
  await until(()=>evaluate("document.querySelector('[data-account-metadata]')?.dataset.accountMetadata==='ready'&&document.querySelector('.account-name-form')?.textContent.includes('Saved name: Scoped account Ω')"),'native account-name submit acknowledged');
  const firstMetadata=await readMetadata();
  assert.deepEqual(firstMetadata,{...metadataBefore,label:'Scoped account Ω',revision:metadataBefore.revision+1});
  await fill('settings-account-name','Local account proposal Ω');
  const otherTabMetadata=await evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();const csrf=await(await fetch('/api/session/csrf/')).json();const r=await fetch(${JSON.stringify(metadataPath)},{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrf.csrf_token,'X-Maipa-Agent-Actor':String(s.user.id)},body:JSON.stringify({expected_revision:${firstMetadata.revision},label:'Other tab account'})});return {status:r.status,value:await r.json()};})()`);
  assert.equal(otherTabMetadata.status,200);assert.equal(otherTabMetadata.value.revision,firstMetadata.revision+1);
  await click('Save account name');
  await until(()=>evaluate("document.querySelector('[data-account-metadata]')?.dataset.accountMetadata==='conflict'"),'stale metadata save refuses without replacing local input');
  assert.equal(await evaluate("document.getElementById('settings-account-name').value"),'Local account proposal Ω');
  await click('Reload saved name');
  await until(()=>evaluate("document.querySelector('[data-account-metadata]')?.dataset.accountMetadata==='conflict'&&document.querySelector('.account-name-form')?.textContent.includes('Saved name: Other tab account')"),'current metadata readback presented for explicit review');
  assert.equal(await evaluate("document.getElementById('settings-account-name').value"),'Local account proposal Ω');
  assert.equal(await evaluate("[...document.querySelectorAll('.account-name-form button')].find(b=>b.textContent==='Save account name').disabled"),true);
  await click('Keep my name');await click('Save account name');
  await until(()=>evaluate("document.querySelector('[data-account-metadata]')?.dataset.accountMetadata==='ready'&&document.querySelector('.account-name-form')?.textContent.includes('Saved name: Local account proposal Ω')"),'human reviewed name saved at current revision');
  const finalMetadata=await readMetadata();assert.deepEqual(finalMetadata,{...metadataBefore,label:'Local account proposal Ω',revision:metadataBefore.revision+3});
  const afterMetadataChat=await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':String(s.user.id),'X-Maipa-Agent-Models':'1'}})).json());})()");
  for(const key of ['id','revision','messages','ui','connection','models'])assert.deepEqual(afterMetadataChat[key],originalMetadataChat[key]);
  assert.equal(await evaluate("window.maipaMetadataComposer===document.getElementById('maipa-chat-message')&&document.getElementById('maipa-chat-message').value==='Unsent settings follow-up Ω'"),true);
  const metadataScreenshot=await command('Page.captureScreenshot',{format:'png'});writeFileSync('artifacts/ci/account-metadata.png',Buffer.from(metadataScreenshot.data,'base64'));
  await command('Page.reload');await preferencesReady();
  await until(()=>evaluate("document.querySelector('[data-account-metadata]')?.dataset.accountMetadata==='ready'&&document.getElementById('settings-account-name')?.value==='Local account proposal Ω'&&document.getElementById('maipa-chat-message')?.value==='Unsent settings follow-up Ω'"),'owned display name and original unsent chat draft survive reload');
  assert.deepEqual(await readMetadata(),finalMetadata);
  record('agent-owned-account-metadata');
  await until(()=>evaluate("document.querySelector('[data-model-preference]')?.dataset.modelPreference==='ready'"),'original future model preference loaded');
  const preferencePath='/api/agent/accounts/'+settingsAfter.connection.account_id+'/model-preference/';
  const readPreference=()=>evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();const r=await fetch(${JSON.stringify(preferencePath)},{cache:'no-store',headers:{'X-Maipa-Agent-Actor':String(s.user.id)}});if(r.status!==200)throw Error('Owned preference read refused');return r.json();})()`);
  const preferenceChat=()=>evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':String(s.user.id),'X-Maipa-Agent-Models':'1'}})).json();})()");
  const preferenceBefore=await readPreference(),preferenceChatBefore=await preferenceChat();
  assert.equal(preferenceBefore.model_id,null);assert.equal(preferenceBefore.revision,0);
  await evaluate("window.maipaPreferenceComposer=document.getElementById('maipa-chat-message');true");
  await click('Remember current model');
  await until(()=>evaluate("document.querySelector('[data-model-preference]')?.dataset.modelPreference==='ready'&&document.querySelector('[data-model-preference]')?.dataset.modelPreferenceRevision==='1'"),'actual current model remembered');
  const preferenceSaved=await readPreference();assert.equal(preferenceSaved.model_id,settingsNext);assert.equal(preferenceSaved.revision,1);
  assert.deepEqual(await preferenceChat(),preferenceChatBefore);
  assert.equal(await evaluate("window.maipaPreferenceComposer===document.getElementById('maipa-chat-message')&&document.getElementById('maipa-chat-message').value==='Unsent settings follow-up Ω'"),true);
  const reconnectPreference=async(expected)=>{
    await click('Disconnect');await until(()=>evaluate("document.querySelector('.chat-status [role=status]')?.textContent==='Connect your agent to begin'&&!document.getElementById('maipa-agent-provider').disabled"),'preference original worker stopped');
    await click('Connect Codex');await until(()=>evaluate(`document.querySelector('.chat-status [role=status]')?.textContent==='Ready'&&document.getElementById('maipa-agent-model')?.value===${JSON.stringify(expected)}&&!document.getElementById('maipa-agent-model').disabled`),'new session confirms intended model');
    await click('Agent settings');await until(()=>evaluate("document.querySelector('[data-agent-settings]')?.dataset.agentSettings==='ready'&&document.querySelector('[data-model-preference]')?.dataset.modelPreference==='ready'"),'shared settings after explicit reconnect');
  };
  await reconnectPreference(settingsNext);
  const preferenceReconnected=await preferenceChat();assert.equal(preferenceReconnected.id,preferenceChatBefore.id);assert.deepEqual(preferenceReconnected.messages,preferenceChatBefore.messages);assert.equal(preferenceReconnected.models.currentModelId,settingsNext);
  assert.deepEqual(await readMetadata(),finalMetadata);assert.deepEqual(await readPreference(),preferenceSaved);
  await click('Use provider default');
  await until(()=>evaluate("document.querySelector('[data-model-preference]')?.dataset.modelPreference==='ready'&&document.querySelector('[data-model-preference]')?.dataset.modelPreferenceRevision==='2'"),'future default acknowledged');
  assert.deepEqual(await preferenceChat(),preferenceReconnected);assert.equal((await readPreference()).model_id,null);
  await reconnectPreference('synthetic-a');
  assert.equal((await preferenceChat()).models.currentModelId,'synthetic-a');assert.deepEqual(await readMetadata(),finalMetadata);
  assert.equal(await evaluate("document.getElementById('maipa-chat-message').value==='Unsent settings follow-up Ω'"),true);
  await command('Page.reload');await preferencesReady();
  await until(()=>evaluate("document.querySelector('[data-model-preference]')?.dataset.modelPreference==='ready'&&document.querySelector('[data-model-preference]')?.dataset.modelPreferenceRevision==='2'&&document.getElementById('settings-model')?.value==='synthetic-a'&&document.getElementById('maipa-chat-message')?.value==='Unsent settings follow-up Ω'"),'future default, current model and unsent draft reload');
  assert.deepEqual((await preferenceChat()).messages,preferenceChatBefore.messages);
  record('agent-future-model-preference');
  phase = 'workflow_editor';
  await requestComponent('Build a shared workflow', 'Maipa.Workflow');
  const workflowReady=()=>until(()=>evaluate("document.querySelector('[data-workflow-phase]')?.dataset.workflowPhase==='ready' && document.querySelectorAll('.workflow-workspace .react-flow__node').length===3"),'shared controlled workflow ready');
  await workflowReady();
  const readWorkflow=()=>evaluate("(async()=>{const id=document.querySelector('[data-workflow-draft]').dataset.workflowDraft;const s=await(await fetch('/api/session/')).json();const r=await fetch('/api/workflows/drafts/'+id+'/',{headers:{'X-Maipa-Workflow-Actor':String(s.user.id)}});if(r.status!==200)throw Error('Owned workflow read failed');return r.json();})()");
  const fillWorkflowInput=async value=>{
    const id=`workflow-run-input-${builtWorkflow.id}`;
    assert.equal(await evaluate(`(()=>{const f=document.getElementById(${JSON.stringify(id)});if(f?.tagName!=='TEXTAREA')return false;f.focus();return document.activeElement===f;})()`),true);
    await command('Input.dispatchKeyEvent',{type:'keyDown',key:'a',windowsVirtualKeyCode:65,modifiers:2});
    await command('Input.dispatchKeyEvent',{type:'keyUp',key:'a',windowsVirtualKeyCode:65,modifiers:2});
    await command('Input.insertText',{text:JSON.stringify(value)});
    assert.equal(await evaluate(`document.getElementById(${JSON.stringify(id)}).value`),JSON.stringify(value));
  };
  const startWorkflow=async()=>{
    await until(()=>evaluate("(()=>{const b=document.querySelector('.workflow-execution .ff-run-controls__btn--run');return b&&!b.disabled&&!b.closest('fieldset')?.disabled;})()"),'actual worker ready');
    await click('▶ Run');
  };
  const builtWorkflow=await readWorkflow();
  assert.equal(builtWorkflow.graph.edges.length,2);
  const transformNode=builtWorkflow.graph.nodes.find(n=>n.data.kind==='@particle-academy/transform');
  assert.deepEqual(transformNode.data.config,{mode:'fields',fields:[{key:'greeting',value:'Hello {{ in.name }}'}]});
  assert.equal(await evaluate("!!document.querySelector('.workflow-workspace .react-flow')"),true);
  const nodeDrag=await evaluate(`(()=>{const n=document.querySelector('.workflow-workspace .react-flow__node[data-id="${transformNode.id}"]');const r=n.querySelector('.ff-node__header').getBoundingClientRect();const x=Math.round(r.x+r.width/2),y=Math.round(r.y+r.height/2);return {x,y,hit:n.contains(document.elementFromPoint(x,y)),draggable:n.classList.contains('draggable')};})()`);
  assert.equal(nodeDrag.hit,true,'native pointer reaches the public workflow node');
  assert.equal(nodeDrag.draggable,true,'public workflow node permits human dragging');
  await command('Input.dispatchMouseEvent',{type:'mouseMoved',x:nodeDrag.x,y:nodeDrag.y});
  await command('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',buttons:1,clickCount:1,x:nodeDrag.x,y:nodeDrag.y});
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>resolve(true)))');
  for(const fraction of [0.25,0.5,1]){
    await command('Input.dispatchMouseEvent',{type:'mouseMoved',button:'left',buttons:1,x:nodeDrag.x+24*fraction,y:nodeDrag.y+32*fraction});
    await evaluate('new Promise(resolve=>requestAnimationFrame(()=>resolve(true)))');
  }
  await command('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,x:nodeDrag.x+24,y:nodeDrag.y+32});
  try {
    await until(async()=>{const saved=await readWorkflow();return saved.revision>builtWorkflow.revision&&JSON.stringify(saved.graph.nodes.find(n=>n.id===transformNode.id).position)!==JSON.stringify(transformNode.position);},'native human node position saved');
  } catch(error) {
    console.error('Owned synthetic workflow drag diagnostic',JSON.stringify({before:transformNode.position,
      after:(await readWorkflow()).graph.nodes.find(n=>n.id===transformNode.id).position,
      surface:await evaluate("(()=>{const w=document.querySelector('.workflow-workspace');return {phase:w?.dataset.workflowPhase,revision:Number(w?.dataset.workflowRevision),selected:document.querySelectorAll('.workflow-workspace .react-flow__node.selected').length};})()") }));
    const failedWorkflow=await command('Page.captureScreenshot',{format:'png'});
    writeFileSync('artifacts/ci/workflow-editor-failure.png',Buffer.from(failedWorkflow.data,'base64'));throw error;
  }
  await workflowReady();
  const humanWorkflow=await readWorkflow();
  await evaluate("window.maipaWorkflowChat=document.querySelector('[data-pane=agent]');true");
  await typeChat('Update the shared workflow');await click('Send');
  await typeChat('Synthetic unsent workflow follow-up');
  await waitForAgent('Maipa.Workflow');
  await until(()=>evaluate("document.querySelector('.workflow-workspace')?.textContent.includes('Native revised step')"),'accepted native update reaches the same public canvas');
  await workflowReady();
  const nativeWorkflow=await readWorkflow();
  assert.equal(nativeWorkflow.id,humanWorkflow.id);assert.ok(nativeWorkflow.revision>humanWorkflow.revision);
  assert.equal(nativeWorkflow.graph.nodes.find(n=>n.id===transformNode.id).data.label,'Native revised step');
  assert.deepEqual(nativeWorkflow.graph.nodes.find(n=>n.id===transformNode.id).position,humanWorkflow.graph.nodes.find(n=>n.id===transformNode.id).position);
  assert.equal(await evaluate("document.querySelector('[data-pane=agent]')===window.maipaWorkflowChat && document.getElementById('maipa-chat-message').value==='Synthetic unsent workflow follow-up'"),true);
  const workflowScreenshot=await command('Page.captureScreenshot',{format:'png'});
  writeFileSync('artifacts/ci/workflow-editor.png',Buffer.from(workflowScreenshot.data,'base64'));
  await command('Page.reload');await preferencesReady();await workflowReady();
  assert.deepEqual(await readWorkflow(),nativeWorkflow);
  assert.equal(await evaluate("!document.querySelector('[data-native-workflow-activity]')"),true,'reload does not replay native authoring activity');
  record('shared-workflow-editor-and-native-authoring');
  await typeChat('Synthetic unsent workflow follow-up');
  await click('Publish version');
  await until(async()=>{const saved=await readWorkflow();return saved.publication?.draft_revision===saved.revision;},'human publication acknowledged');
  await openRunDetails();await fillWorkflowInput({name:'Ada'});
  await startWorkflow();
  await until(()=>evaluate("document.querySelector('[data-workflow-run-status]')?.dataset.workflowRunStatus==='completed'"),'real server workflow completes');
  const readRun=()=>evaluate("(async()=>{const id=document.querySelector('[data-workflow-run]').dataset.workflowRun;const s=await(await fetch('/api/session/')).json();const r=await fetch('/api/workflows/runs/'+id+'/',{headers:{'X-Maipa-Workflow-Actor':String(s.user.id)}});if(r.status!==200)throw Error('Owned run read failed');return r.json();})()");
  const completedWorkflow=await readRun();
  const outputNode=nativeWorkflow.graph.nodes.find(n=>n.data.kind==='@particle-academy/output');
  assert.deepEqual(completedWorkflow.run.outputs[outputNode.id],{greeting:'Hello Ada'});
  assert.deepEqual(Object.values(completedWorkflow.run.nodes).map(n=>n.attempts),[1,1,1]);
  assert.equal(await evaluate("document.getElementById('maipa-chat-message').value"),'Synthetic unsent workflow follow-up');
  await command('Page.reload');await preferencesReady();await workflowReady();
  await until(()=>evaluate("document.querySelector('[data-workflow-run-status]')?.dataset.workflowRunStatus==='completed'"),'saved run survives reload');
  assert.deepEqual((await readRun()).run,completedWorkflow.run);
  await typeChat('Add workflow approval');await click('Send');await typeChat('Synthetic unsent workflow follow-up');await waitForAgent('Maipa.Workflow');
  await until(()=>evaluate("document.querySelector('[data-workflow-phase]')?.dataset.workflowPhase==='ready' && document.querySelectorAll('.workflow-workspace .react-flow__node').length===4"),'shared approval node saved');
  await click('Publish version');
  await until(async()=>{const saved=await readWorkflow();return saved.publication?.draft_revision===saved.revision;},'approval publication acknowledged');
  await openRunDetails();await fillWorkflowInput({name:'Ada',approved:true});
  await startWorkflow();await until(()=>evaluate("!!document.querySelector('[data-workflow-gate]')"),'real durable human gate visible');
  const waitingWorkflow=await readRun();assert.equal(waitingWorkflow.run.status,'waiting');assert.equal(waitingWorkflow.gate.kind,'human_approval');
  assert.equal(Object.hasOwn(waitingWorkflow.run.outputs,outputNode.id),false);
  assert.equal(await evaluate("[...document.querySelectorAll('.workflow-execution textarea')].some(t=>t.value.includes('Hello Ada'))"),true);
  await click('Approve');await until(()=>evaluate("document.querySelector('[data-workflow-run-status]')?.dataset.workflowRunStatus==='completed'"),'human approval recorded and run completed');
  const approvedWorkflow=await readRun();assert.equal(approvedWorkflow.gate,null);assert.deepEqual(approvedWorkflow.run.outputs[outputNode.id],{greeting:'Hello Ada'});
  await startWorkflow();await until(()=>evaluate("!!document.querySelector('[data-workflow-gate]')"),'second actual gate visible');
  const cancelledWorkflowId=(await readRun()).run.id;assert.notEqual(cancelledWorkflowId,approvedWorkflow.run.id);
  await click('⏹ Cancel');await until(()=>evaluate("document.querySelector('[data-workflow-run-status]')?.dataset.workflowRunStatus==='cancelled'"),'human cancellation persisted');
  const cancelledWorkflow=await readRun();assert.equal(cancelledWorkflow.gate,null);assert.equal(Object.hasOwn(cancelledWorkflow.run.outputs,outputNode.id),false);
  assert.equal(await evaluate("document.getElementById('maipa-chat-message').value"),'Synthetic unsent workflow follow-up');
  record('shared-workflow-published-run-and-reload');
  phase = 'preferences';
  await preferencesReady();
  await click('Dark'); await preferencesReady();
  await requestComponent('Present a note', 'Maipa.Note'); await preferencesReady();
  await command('Page.reload'); await preferencesReady();
  await until(() => evaluate("document.getElementById('maipa-theme-dark')?.getAttribute('aria-pressed') === 'true' && !!document.querySelector('.agent-commentary')"), 'saved preferences after reload');
  const winner = await evaluate("(async()=>{const p=await(await fetch('/api/workspace/preferences/')).json();const s=await(await fetch('/api/session/csrf/')).json();const r=await fetch('/api/workspace/preferences/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Maipa-Preferences':p.preferences_id},body:JSON.stringify({expected_revision:p.revision,theme:'light',active_view:'workspace-guide'})});return {status:r.status,body:await r.json()};})()");
  assert.equal(winner.status, 200);
  await click('System');
  await until(() => evaluate("document.querySelector('[data-preferences-state]')?.dataset.preferencesState === 'conflict'"), 'stale preferences rejected');
  assert.equal(await evaluate("document.getElementById('maipa-theme-dark').disabled"), true);
  assert.deepEqual(await evaluate("(async()=> (await fetch('/api/workspace/preferences/')).json())()"), winner.body);
  await click('Reload saved preferences'); await preferencesReady();
  await until(() => evaluate("document.getElementById('maipa-theme-light')?.getAttribute('aria-pressed') === 'true'"), 'authoritative theme restored');
  await click('Dark'); await preferencesReady();
  await requestComponent('Open client facts', 'Maipa.ClientFacts'); await preferencesReady();
  await until(() => evaluate(`document.querySelector('[data-entity-id]')?.dataset.entityId === ${JSON.stringify(clientEntityId)}`), 'facts available after preference recovery');
  record('preferences-save-conflict-recovery');
  phase = 'layout';
  const layoutReady = () => until(() => evaluate("!!document.querySelector('.agent-workspace') && document.querySelector('[data-layout-state]')?.dataset.layoutState === 'ready'"), 'saved layout ready');
  await layoutReady();
  const layoutBaseline = await evaluate("(async()=> (await(await fetch('/api/workspace/layout/')).json()).revision)()");
  assert.equal(await evaluate("Number(document.querySelector('[role=separator]').getAttribute('aria-valuenow'))"), 60);
  assert.equal(await evaluate("(() => { const s=document.querySelector('[role=separator]'); s.scrollIntoView({block:'center'}); s.focus(); return document.activeElement===s; })()"), true);
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'ArrowRight', windowsVirtualKeyCode: 39 });
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'ArrowRight', windowsVirtualKeyCode: 39 });
  await until(() => evaluate("(async()=> document.querySelector('[data-layout-state]')?.dataset.layoutState === 'ready' && (await(await fetch('/api/workspace/layout/')).json()).split_basis_points === 6500)()"), 'keyboard width persisted');
  await layoutReady();
  assert.equal(await evaluate("(async()=> (await(await fetch('/api/workspace/layout/')).json()).split_basis_points)()"), 6500);
  const drag = await evaluate("(() => { const s=document.querySelector('[role=separator]'); const r=s.getBoundingClientRect(); const p=document.querySelector('.agent-shell-panels').getBoundingClientRect(); const x=Math.ceil(r.x),y=Math.round(r.y+r.height/2); return {x,y,target:Math.round(p.x+p.width*.37),hit:document.elementFromPoint(x,y)===s}; })()");
  assert.equal(drag.hit, true, 'Native pointer hits the actual separator');
  await command('Input.dispatchMouseEvent', { type: 'mouseMoved', x: drag.x, y: drag.y });
  await command('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', buttons: 1, clickCount: 1, x: drag.x, y: drag.y });
  await command('Input.dispatchMouseEvent', { type: 'mouseMoved', button: 'left', buttons: 1, x: drag.target, y: drag.y });
  await command('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, x: drag.target, y: drag.y });
  try {
    await until(() => evaluate(`(async()=> document.querySelector('[data-layout-state]')?.dataset.layoutState === 'ready' && (await(await fetch('/api/workspace/layout/')).json()).revision === ${layoutBaseline + 2})()`), 'pointer width persisted');
  } catch (error) {
    console.error('Owned synthetic pointer diagnostic', JSON.stringify({ drag, observed: await evaluate("(async()=> {const l=await(await fetch('/api/workspace/layout/')).json();const s=document.querySelector('[role=separator]');return {phase:document.querySelector('[data-layout-state]')?.dataset.layoutState, value:s.getAttribute('aria-valuenow'), disabled:s.getAttribute('aria-disabled'), revision:l.revision, points:l.split_basis_points};})()") }));
    throw error;
  }
  await layoutReady();
  const dragged = await evaluate("(async()=> ({layout:await(await fetch('/api/workspace/layout/')).json(),value:Number(document.querySelector('[role=separator]').getAttribute('aria-valuenow'))}))()");
  assert.ok(dragged.layout.split_basis_points >= 3670 && dragged.layout.split_basis_points <= 3730);
  assert.equal(dragged.layout.split_basis_points, Math.round(dragged.value * 100));
  assert.equal(dragged.layout.revision, layoutBaseline + 2);
  const concurrentLayout = await evaluate("(async()=> {const l=await(await fetch('/api/workspace/layout/')).json(); const s=await(await fetch('/api/session/csrf/')).json(); const r=await fetch('/api/workspace/layout/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Maipa-Layout':l.layout_id},body:JSON.stringify({expected_revision:l.revision,split_basis_points:3500})}); return {status:r.status,body:await r.json()};})()");
  assert.equal(concurrentLayout.status, 200);
  await evaluate("document.querySelector('[role=separator]').focus(); true");
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'End', windowsVirtualKeyCode: 35 });
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'End', windowsVirtualKeyCode: 35 });
  await until(() => evaluate("document.querySelector('[data-layout-state]')?.dataset.layoutState === 'conflict'"), 'stale width refused');
  assert.equal(await evaluate("document.querySelector('[role=separator]').getAttribute('aria-disabled')"), 'true');
  assert.equal(await evaluate("(async()=> (await(await fetch('/api/workspace/layout/')).json()).split_basis_points)()"), 3500);
  await click('Reload saved layout');
  await layoutReady();
  assert.equal(await evaluate("Number(document.querySelector('[role=separator]').getAttribute('aria-valuenow'))"), 35);
  record('layout-gestures-conflict-recovery');
  phase = "intake_facts";
  await select("intake-fact-key", "records_complete");
  await select("intake-knowledge", "known");
  await until(() => evaluate("!!document.getElementById('intake-recorded-value')"), "known boolean control");
  await select("intake-recorded-value", "false");
  await fill("intake-evidence-reference", "synthetic-browser-source-1");
  await fill("intake-evidence-locator", "page 1");
  await click("Save fact revision");
  await until(() => evaluate("document.querySelector('[data-entity-revision]')?.dataset.entityRevision === '1'"), "recorded known false");
  assert.equal(await evaluate("document.querySelector('[data-fact-key=records_complete] dd').textContent"), "No");
  await select("intake-fact-key", "legal_form");
  await select("intake-knowledge", "known");
  await until(() => evaluate("!!document.getElementById('intake-recorded-value')"), "known legal-form control");
  await select("intake-recorded-value", "llc");
  await fill("intake-evidence-reference", "synthetic-browser-source-1");
  await fill("intake-evidence-locator", "page 2");
  await click("Save fact revision");
  await until(() => evaluate("document.querySelector('[data-entity-revision]')?.dataset.entityRevision === '2'"), "recorded LLC");
  assert.equal(await evaluate("document.querySelector('[data-fact-key=legal_form] dd').textContent"), "LLC");
  assert.equal(await evaluate("document.querySelector('[data-fact-key=tax_classification] dd').textContent"), "Unknown");
  assert.equal(await evaluate("document.querySelector('[data-fact-key=tax_elections] dd').textContent"), "Unknown");
  record("intake-facts-and-evidence");
  phase = "intake_history";
  await select("intake-recorded-value", "corporation");
  await fill("intake-evidence-reference", "synthetic-browser-correction-2");
  await click("Save fact revision");
  await until(() => evaluate("document.querySelector('[data-entity-revision]')?.dataset.entityRevision === '3'"), "corrected legal form");
  await until(() => evaluate("document.querySelectorAll('[aria-label=\"Fact history\"] li').length === 2"), "both historical assertions");
  const historyValues = await evaluate("[...document.querySelectorAll('[aria-label=\"Fact history\"] li')].map(row=>row.innerText)");
  assert.ok(historyValues[0].includes("Corporation") && historyValues[0].includes("synthetic-browser-correction-2"));
  assert.ok(historyValues[1].includes("LLC") && historyValues[1].includes("synthetic-browser-source-1"));
  const concurrent = await evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();
    const on=document.getElementById('intake-effective-from').value;
    const r=await fetch('/api/intake/entities/'+${JSON.stringify(clientEntityId)}+'/facts/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token},
      body:JSON.stringify({key:'industry',knowledge:'known',value:'Synthetic concurrent fact',expected_revision:3,
        effective_from:on,effective_to:null,evidence:{reference:'synthetic-concurrent-source',locator:'record 1',observed_on:on}})});
    return {status:r.status,body:await r.json()};})()`);
  assert.equal(concurrent.status, 201); assert.equal(concurrent.body.revision, 4);
  await select("intake-recorded-value", "partnership");
  await click("Save fact revision");
  await until(() => evaluate("document.querySelector('[aria-label=\"Record client fact\"] [role=alert]')?.textContent.includes('This record changed')"), "stale-edit refusal");
  assert.equal(await evaluate("[...document.querySelectorAll('button, summary')].find(b=>b.textContent==='Save fact revision').disabled"), true);
  const actual = await evaluate(`(async()=> (await fetch('/api/intake/entities/'+${JSON.stringify(clientEntityId)}+'/')).json())()`);
  assert.equal(actual.revision, 4); assert.equal(actual.facts.legal_form.value, "corporation");
  await click("Reload current record");
  await until(() => evaluate("document.querySelector('[data-entity-revision]')?.dataset.entityRevision === '4'"), "honest conflict recovery");
  assert.equal(await evaluate("document.querySelector('[data-fact-key=legal_form] dd').textContent"), "Corporation");
  record("intake-history-and-conflicts");
  phase = 'authority';
  await click('Review authority');
  await until(() => evaluate("document.querySelector('.authority-grant')?.textContent.includes('synthetic-browser-authority')"), 'actual scoped authority');
  const authorityBefore = await evaluate(`(async()=> (await fetch('/api/policies/entities/'+${JSON.stringify(clientEntityId)}+'/')).json())()`);
  assert.equal(authorityBefore.financial_execution_available, false);
  assert.equal(authorityBefore.grants.length, 1);
  assert.equal(authorityBefore.grants[0].current, true);
  assert.equal(authorityBefore.grants[0].limit, '500.0000');
  assert.equal(await evaluate("document.querySelector('[data-fact-key=legal_form] dd').textContent"), 'Corporation');
  const deniedRevocation = await evaluate(`(async()=> (await fetch('/api/policies/grants/'+${JSON.stringify(authorityBefore.grants[0].id)}+'/revoke/',
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({reason:'synthetic missing csrf'})})).status)()`);
  assert.equal(deniedRevocation, 403);
  await fill('authority-reason-'+authorityBefore.grants[0].id, 'Synthetic browser withdrawal');
  await click('Revoke authority');
  await until(() => evaluate("document.querySelector('.authority-grant')?.textContent.includes('Revoked') && !document.querySelector('.authority-grant form')"), 'persisted revoked authority');
  const authorityAfter = await evaluate(`(async()=> (await fetch('/api/policies/entities/'+${JSON.stringify(clientEntityId)}+'/')).json())()`);
  assert.equal(authorityAfter.grants[0].current, false);
  assert.ok(authorityAfter.grants[0].revoked_at);
  assert.equal(authorityAfter.policy_version, authorityBefore.policy_version+1);
  await select('intake-entity', secondEntity.body.id);
  await until(() => evaluate(`document.querySelector('[data-entity-id]')?.dataset.entityId === ${JSON.stringify(secondEntity.body.id)}`), 'separate authority scope');
  await click('Review authority');
  await until(() => evaluate("document.querySelector('.authority-panel')?.textContent.includes('No financial or workflow authority is recorded')"), 'honest absent authority');
  assert.equal(await evaluate("document.querySelector('.authority-panel').textContent.includes('synthetic-browser-authority')"), false);
  record('client-authority-and-revocation');
  await preferencesReady();
  await requestComponent('Present a note', 'Maipa.Note'); await preferencesReady();
  const ownerPreferences = await evaluate("(async()=> (await fetch('/api/workspace/preferences/')).json())()");
  phase='agent_shell';
  await typeChat('Synthetic provider rate limit test');await click('Send');
  await until(()=>evaluate("document.querySelector('.chat-status [role=status]')?.textContent==='Provider usage limit reached' && !document.getElementById('maipa-agent-provider').disabled"),'closed reported cooldown after verified worker release');
  await typeChat('Draft kept during provider cooldown Ω');
  assert.equal(await evaluate("document.querySelector('.chat-composer button[aria-label=Send]').disabled"),true);
  await click('Connection');
  await until(()=>evaluate("document.querySelector('[data-turn-failure=rate_limit] time')?.getAttribute('datetime')"),'public Fancy connection notice exposes only reported reset');
  const failedTurn=await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id,'X-Maipa-Agent-Failures':'1'}})).json();})()");
  assert.equal(failedTurn.failure.kind,'rate_limit');assert.equal(typeof failedTurn.failure.resets_at_ms,'number');
  assert.equal(await evaluate("document.body.textContent.includes('PRIVATE_RPC_MESSAGE')||document.body.textContent.includes('PRIVATE_PROVIDER_REASON')||document.body.textContent.includes('PRIVATE_RAW')"),false);
  const blockedSend=await evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();return(await fetch('/api/agent/chat/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Maipa-Agent-Actor':s.user.id,'X-Maipa-Agent-Failures':'1'},body:JSON.stringify({action:'send',revision:${failedTurn.revision},selection_revision:${failedTurn.connection.revision},request_id:crypto.randomUUID(),text:'Synthetic forbidden cooldown retry'})})).status;})()`);
  assert.equal(blockedSend,429);
  assert.deepEqual(await evaluate("(async()=>{const s=await(await fetch('/api/session/')).json();return(await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id}})).json()).messages;})()"),failedTurn.messages);
  await checkChatViewport(897,264);await checkChatViewport(390,480);
  await command('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});
  assert.equal(await evaluate("document.querySelector('#maipa-chat-message').value"),'Draft kept during provider cooldown Ω');
  const failureScreenshot=await command('Page.captureScreenshot',{format:'png'});
  writeFileSync('artifacts/ci/turn-failure.png',Buffer.from(failureScreenshot.data,'base64'));
  phase = "logout_viewer";
  checkpoint = 'logout_click';
  await click("Sign out");
  checkpoint = 'logout_form';
  await until(() => evaluate("!!document.getElementById('maipa-username')"), "logged-out password form");
  checkpoint = 'logout_session';
  assert.equal(await evaluate("(async()=> (await fetch('/api/session/')).status)()"), 401);
  checkpoint = 'logout_notes';
  if (await evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')")) await click("Got it");
  await login(fixture.viewer_username);
  checkpoint = 'viewer_scope';
  const viewer = await evaluate(`(async()=>{const s=await (await fetch('/api/session/')).json();
    const role=await (await fetch('/api/workspace/?role=administrator')).json();
    const denied=await fetch('/api/agent/accounts/'+${JSON.stringify(fixture.foreign_account_id)}+'/transport-checks/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Role':'administrator'},body:'{}'});
    const history=await (await fetch('/api/agent/accounts/'+${JSON.stringify(fixture.foreign_account_id)}+'/transport-checks/')).json();
    const foreign=await fetch('/api/agent/accounts/'+${JSON.stringify(fixture.account_id)}+'/transport-checks/');
    return {role:role.role,denied:denied.status,error:await denied.json(),history,foreign:foreign.status};})()`);
  checkpoint = 'viewer_assertions';
  assert.equal(viewer.role, "viewer"); assert.equal(viewer.denied, 403);
  assert.equal(await evaluate("document.querySelectorAll('.header-shortcuts button').length"),0);
  assert.deepEqual(viewer.error, { error: { code: "permission_denied" } });
  assert.deepEqual(viewer.history, { checks: [] }); assert.equal(viewer.foreign, 404);
  record("logout-and-viewer-denial");
  await preferencesReady();
  const viewerPreferences = await evaluate("(async()=> (await fetch('/api/workspace/preferences/')).json())()");
  assert.notEqual(viewerPreferences.preferences_id, ownerPreferences.preferences_id);
  assert.equal(viewerPreferences.theme, 'system');
  assert.equal(viewerPreferences.active_view, 'client-facts');
  await click('Light'); await preferencesReady();
  phase = "intake_scope";
  const privateIntake = await evaluate(`(async()=>{
    const listed=await(await fetch('/api/intake/engagements/?role=administrator&tenant_id=foreign')).json();
    const entity=await fetch('/api/intake/entities/'+${JSON.stringify(clientEntityId)}+'/');
    const history=await fetch('/api/intake/entities/'+${JSON.stringify(clientEntityId)}+'/facts/legal_form/history/');
    const authority=await fetch('/api/policies/entities/'+${JSON.stringify(clientEntityId)}+'/');
    return {listed,entity:entity.status,history:history.status,authority:authority.status};})()`);
  assert.deepEqual(privateIntake.listed, {engagements:[]});
  assert.equal(privateIntake.entity, 404); assert.equal(privateIntake.history, 404);
  assert.equal(privateIntake.authority, 404);
  assert.equal(await evaluate("document.body.innerText.includes('Synthetic browser entity') || document.body.innerText.includes('synthetic-browser-source-1')"), false);
  record("intake-user-isolation");
  phase = 'layout';
  await layoutReady();
  assert.equal(await evaluate("Number(document.querySelector('[role=separator]').getAttribute('aria-valuenow'))"), 60);
  assert.equal(await evaluate("getComputedStyle(document.querySelector('[role=separator]')).display"), 'none');
  const viewerWidth = await evaluate("(async()=>{const l=await(await fetch('/api/workspace/layout/')).json();const s=await(await fetch('/api/session/csrf/')).json();const r=await fetch('/api/workspace/layout/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Maipa-Layout':l.layout_id},body:JSON.stringify({expected_revision:l.revision,split_basis_points:7200})});return{status:r.status,body:await r.json()};})()");
  assert.equal(viewerWidth.status, 200);
  assert.equal(viewerWidth.body.split_basis_points, 7200);
  await click('Sign out');
  await until(() => evaluate("!!document.getElementById('maipa-username')"), 'layout logout');
  if (await evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')")) await click('Got it');
  assert.equal(await evaluate("document.querySelector('[role=separator]')"), null);
  await login(fixture.username);
  await layoutReady();
  assert.equal(await evaluate("Number(document.querySelector('[role=separator]').getAttribute('aria-valuenow'))"), 35);
  await command('Page.reload');
  await layoutReady();
  assert.equal(await evaluate("Number(document.querySelector('[role=separator]').getAttribute('aria-valuenow'))"), 35);
  record('layout-user-isolation-and-login');
  await preferencesReady();
  assert.deepEqual(await evaluate("(async()=> (await fetch('/api/workspace/preferences/')).json())()"), ownerPreferences);
  await until(() => evaluate("document.getElementById('maipa-theme-dark')?.getAttribute('aria-pressed') === 'true' && !!document.querySelector('.chat-only')"), 'owner preferences restored after other account');
  assert.equal(await evaluate("document.querySelector('.chat-status [role=status]')?.textContent"),'Provider usage limit reached');
  record('preferences-user-isolation');
  }
  phase = "browser_exceptions";
  assert.equal(exceptions.length, 0, "No uncaught browser exceptions");
  record("no-uncaught-exceptions");
  evidence = { checks: passed.length, scenarios: passed, build_id: fixture.build_id, browser: version.Browser, cleanup: false };
} catch (error) {
  failedPhase = phase;
  if (['admin_users', 'work_views', 'agent_shell', 'intake_facts'].includes(phase)) {
    surfaceEvidence = { surface_responses: { ...surfaceResponses }, failure_kind:
      error?.code === 'ERR_ASSERTION' ? 'assertion' :
      error?.message?.startsWith('Timeout:') ? 'timeout' :
      error?.message?.startsWith('CDP ') ? 'protocol' : 'other' };
    try {
      surfaceEvidence.surface_ui = await evaluate(`(() => ({
        admin_panel: !!document.querySelector('[data-access-state]'),
        admin_ready: document.querySelector('[data-access-state]')?.dataset.accessState === 'ready',
        admin_fields: !!document.querySelector('[data-member-id]'),
        conversation: !!document.querySelector('.conversation'),
        contextual_panel: !!document.querySelector('.has-composition'),
        chat_alert: !!document.querySelector('.chat-controls [role=alert]'),
        composer_nonempty: !!document.querySelector('#maipa-chat-message')?.value.trim(),
        composer_focused: document.activeElement === document.querySelector('#maipa-chat-message'),
        send_present: !!document.querySelector('[data-react-fancy-prompt-input-send]'),
        send_disabled: document.querySelector('[data-react-fancy-prompt-input-send]')?.disabled === true,
        ui_phase_ready: document.querySelector('.chat-status [role=status]')?.textContent === 'Ready',
        intake_form: !!document.querySelector('form[aria-label="Record client fact"]'),
        intake_valid: !!document.querySelector('form[aria-label="Record client fact"]')
          && !document.querySelector('form[aria-label="Record client fact"] :invalid'),
        intake_reference: !!document.getElementById('intake-evidence-reference')?.value.trim(),
        intake_locator: !!document.getElementById('intake-evidence-locator')?.value.trim(),
        intake_alert: !!document.querySelector('form[aria-label="Record client fact"] [role=alert]'),
        intake_save_disabled: [...document.querySelectorAll('form[aria-label="Record client fact"] button')]
          .find(b=>b.textContent.trim()==='Save fact revision')?.disabled === true,
        intake_revision_one: document.querySelector('[data-entity-revision]')?.dataset.entityRevision === '1'
      }))()`);
      if (phase === 'agent_shell') {
        surfaceEvidence.agent_state = await evaluate(`(async()=>{
          const s=await(await fetch('/api/session/',{cache:'no-store'})).json();
          const c=await(await fetch('/api/agent/chat/',{cache:'no-store',headers:{'X-Maipa-Agent-Actor':s.user.id}})).json();
          return {phase:c.phase,interruption_received:Array.isArray(c.messages)&&c.messages.some(m=>m.role==='user'&&m.text==='Simulate connection interruption')};
        })()`);
      }
    } catch { /* Keep the original failure and verified cleanup if diagnostics fail. */ }
  }
  if (['login', 'logout_viewer'].includes(phase)) {
    // Only fixed codes, booleans and bounded status numbers cross the CI report.
    // No DOM text, user values, cookies, bodies, paths, URLs or raw errors.
    authEvidence = { auth_responses: { ...authResponses }, failure_kind:
      error?.code === 'ERR_ASSERTION' ? 'assertion' :
      error?.message?.startsWith('Timeout:') ? 'timeout' :
      error?.message === 'Browser evaluation failure' ? 'evaluation' :
      error?.message?.startsWith('CDP ') ? 'protocol' : 'other' };
    try {
      authEvidence.auth_ui = await evaluate(`(() => ({
        login_form: !!document.getElementById('maipa-username'),
        sign_out: !![...document.querySelectorAll('.sign-out-controls button')].find(b=>b.textContent.trim()==='Sign out'),
        sign_out_pending: !!document.querySelector('.sign-out-controls button:disabled'),
        sign_out_failed: !!document.querySelector('.sign-out-controls [role=alert]'),
        release_notes: !!document.querySelector('[aria-label="What\\'s new in Maipa"]')
      }))()`);
    } catch { /* Preserve the original failure and cleanup when diagnostics cannot be read. */ }
  }
  if (phase === "browser_launch") {
    startupEvidence = { startup_state: launchState };
    if (Number.isInteger(launchExit) && launchExit >= -(2 ** 31) && launchExit < 2 ** 32) startupEvidence.launch_exit = launchExit;
    if (["ENOENT", "EACCES", "EPERM", "EINVAL", "ENOMEM", "UNKNOWN"].includes(launchError?.code)) startupEvidence.launch_error = launchError.code;
  }
  throw error;
} finally {
  if(failedPhase==='work_views'){
    try{
      const d=await evaluate(`(async()=>{const r=await fetch('/api/workspace/views/');const s=await r.json();return{status:r.status,revision:s.revision,ids:s.views?.map(v=>v.id),selection:document.getElementById('maipa-saved-view')?.value,views_state:document.querySelector('[data-views-state]')?.dataset.viewsState,select_count:document.querySelectorAll('#maipa-saved-view').length,alerts:[...document.querySelectorAll('.saved-view-bar [role=alert],.maipa-views-popover [role=alert]')].map(e=>e.textContent),move_disabled:document.querySelector('[aria-label="Move saved view later"]')?.disabled};})()`);
      writeFileSync('artifacts/ci/view-draft-failure.json',JSON.stringify(d,null,2));
    }catch{}
  }
  if(failedPhase==='workflow_editor'){
    try{
      const d=await evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();const c=await(await fetch('/api/agent/chat/',{headers:{'X-Maipa-Agent-Actor':s.user.id,'X-Maipa-Agent-Failures':'1'}})).json();const id=document.querySelector('[data-workflow-draft]')?.dataset.workflowDraft;const r=id?await(await fetch('/api/workflows/drafts/'+id+'/execution/',{headers:{'X-Maipa-Workflow-Actor':s.user.id}})).json():null;return{phase:c.phase,failure:c.failure,messages:c.messages?.map(m=>({role:m.role,state:m.state})),draft_state:document.querySelector('[data-draft-state]')?.dataset.draftState,runs:r?.runs?.map(x=>({status:x.status,error:x.error,step:x.step,nodes:x.nodes}))};})()`);
      const detail=await evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();const host=document.querySelector('[data-workflow-draft]');const id=host?.dataset.workflowDraft;if(!id)return {};const r=await(await fetch('/api/workflows/drafts/'+id+'/',{headers:{'X-Maipa-Workflow-Actor':s.user.id}})).json();return {client_phase:host.dataset.workflowPhase,client_revision:host.dataset.workflowRevision,revision:r.revision,publication:r.publication,graph:r.graph};})()`);
      writeFileSync('artifacts/ci/workflow-draft-failure.json',JSON.stringify({...d,...detail},null,2));
      const shot=await command('Page.captureScreenshot',{format:'png'});writeFileSync('artifacts/ci/workflow-draft-failure.png',Buffer.from(shot.data,'base64'));
    }catch{}
  }
  writeFileSync('artifacts/ci/draft-failure-trace.json',JSON.stringify(draftTrace,null,2));
  phase = "cleanup";
  recordProgress();
  clearInterval(progressTimer);
  if (socket?.readyState === WebSocket.OPEN && !closed) {
    cleanupCheckpoint = 'browser_close'; recordProgress();
    try {
      await closeBrowserThroughDebugger(command, child, () => closed);
      await until(() => closed, 'graceful browser exit');
    } catch { /* The bounded force-stop fallback and all cleanup assertions still apply. */ }
  }
  cleanupCheckpoint = 'socket_close'; recordProgress();
  socket?.close();
  cleanupCheckpoint = 'pending_close'; recordProgress();
  for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(Error("Fixture ended")); }
  if (child.pid && !closed) {
    cleanupCheckpoint = 'stop_tree'; recordProgress();
    // Only the newly spawned test browser's process tree is targeted.
    const stopped = spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "pipe", encoding: 'utf8', timeout: 10_000 });
    stopStatus = stopped.status; stopError = stopped.error?.code;
    try { process.kill(child.pid, 0); rootAliveAfterStop = true; } catch { rootAliveAfterStop = false; }
    if (stopped.status !== 0) console.error('Owned synthetic browser stop failed', stopped.stderr || stopped.stdout || stopped.error?.code);
    cleanupCheckpoint = 'browser_exit'; recordProgress();
    await until(() => closed, "browser exit");
  }
  assert.ok(closed || launchError, "Owned browser process exited");
  if (debugOrigin) {
    cleanupCheckpoint = 'endpoint'; recordProgress();
    let reachable = false;
    try { await fetch(`${debugOrigin}/json/version`, { signal: AbortSignal.timeout(1000) }); reachable = true; } catch {}
    assert.equal(reachable, false, "Owned debugging endpoint closed");
  }
  assert.equal(dirname(realpathSync.native(profile)), parent);
  cleanupCheckpoint = 'profile_remove'; recordProgress();
  rmSync(profile, { recursive: true, maxRetries: 5, retryDelay: 200 });
  assert.equal(existsSync(profile), false);
  cleanupVerified = true;
  cleanupCheckpoint = 'complete'; recordProgress();
  if (evidence) { record("owned-browser-cleanup"); evidence.checks = passed.length; evidence.cleanup = true; }
}
console.log(JSON.stringify(evidence));
