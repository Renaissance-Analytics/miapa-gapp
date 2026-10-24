// Owned native-browser fixture only. No installed driver, arbitrary debug URL or human profile.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { ownedProfileRoot } from "./browser-profile.mjs";

let phase = "fixture_input", failedPhase, cleanupVerified = false;
let startupEvidence = {}, launchState = "starting", launchExit;
process.on("uncaughtException", error => {
  // Isolated synthetic fixture diagnostics stay in ignored local failure logs;
  // CI artifacts contain only the fixed phase/cleanup report below.
  console.error(error?.stack || 'Native browser fixture failed');
  console.log(JSON.stringify({ checks: 0, reason: failedPhase || phase, cleanup: cleanupVerified, ...startupEvidence }));
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
const record = id => { assert.ok(!passed.includes(id)); passed.push(id); };
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
  assert.ok(!response.exceptionDetails, "Browser evaluation failure");
  return response.result.value;
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
  const point = await evaluate(`(() => { const b = [...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || b.textContent.trim()) === ${JSON.stringify(label)}); if (!b || b.disabled) return null; b.scrollIntoView({block:'center'}); const r=b.getBoundingClientRect(); return r.width && r.height ? {x:r.x+r.width/2,y:r.y+r.height/2} : null; })()`);
  assert.ok(point, `Visible button ${label}`);
  await command("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...point });
  await command("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...point });
}
async function fill(id, value) {
  assert.equal(await evaluate(`(() => { const field=document.getElementById(${JSON.stringify(id)}); if(!field || field.tagName!=='INPUT')return false; field.focus(); return document.activeElement===field; })()`), true);
  await command('Input.dispatchKeyEvent', { type: 'keyDown', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await command('Input.dispatchKeyEvent', { type: 'keyUp', key: 'a', code: 'KeyA', windowsVirtualKeyCode: 65, modifiers: 2 });
  await command('Input.insertText', { text: value });
}
async function select(id, value) {
  const steps = await evaluate(`(() => { const field=document.getElementById(${JSON.stringify(id)});
    if (!field || field.tagName!=='SELECT' || field.disabled) return -1;
    field.scrollIntoView({block:'center'}); field.focus();
    return [...field.options].filter(o=>!o.disabled).findIndex(o=>o.value===${JSON.stringify(value)}); })()`);
  assert.ok(steps >= 0, 'Selectable recorded option');
  for (const [key, code] of [['Home', 36], ...Array.from({length:steps},()=>['ArrowDown',40])]) {
    await command('Input.dispatchKeyEvent', { type: 'keyDown', key, windowsVirtualKeyCode: code });
    await command('Input.dispatchKeyEvent', { type: 'keyUp', key, windowsVirtualKeyCode: code });
  }
  await until(() => evaluate(`document.getElementById(${JSON.stringify(id)})?.value === ${JSON.stringify(value)}`), 'native select change');
}
try {
  await until(() => {
    if (launchError || closed) throw Error("Native browser launch failed");
    return existsSync(join(profile, "DevToolsActivePort"));
  }, "native browser");
  launchState = "devtools_file";
  const [port, browserPath] = readFileSync(join(profile, "DevToolsActivePort"), "utf8").trim().split(/\r?\n/);
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
    if (message.method === "Runtime.exceptionThrown") exceptions.push(true);
    if (message.method === "Network.responseReceived" && message.params.response.url === `${fixture.origin}/api/intake/engagements/`)
      intakeListResponses.push(message.params.response.status);
    if (message.id && pending.has(message.id)) {
      const entry = pending.get(message.id); pending.delete(message.id); clearTimeout(entry.timer);
      if (message.error) entry.reject(Error(`CDP ${entry.method} rejected: ${String(message.error.message).slice(0, 200)}`)); else entry.resolve(message.result);
    }
  });
  await command("Page.enable"); await command("Runtime.enable"); await command("Network.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: fixture.origin });
  if (fixture.first_run) {
    phase = 'first_run';
    await until(() => evaluate("!!document.getElementById('maipa-admin-username')"), 'first-run administrator form');
    await until(() => evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')"), 'first-run update notes');
    await click('Got it');
    await fill('maipa-setup-code', fixture.setup_code);
    await fill('maipa-admin-username', fixture.username);
    await fill('maipa-admin-password', fixture.password);
    await fill('maipa-admin-confirmation', fixture.password);
    await click('Create administrator');
    await until(() => evaluate("document.body.innerText.includes('Signed in')"), 'created administrator session');
    await until(() => evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')"), 'new-account update notes');
    await click('Got it');
    const admin = await evaluate("(async()=>({workspace:await(await fetch('/api/workspace/')).json(),setup:await(await fetch('/api/setup/')).json()}))()");
    assert.equal(admin.workspace.role, 'administrator');
    assert.equal(admin.setup.setup_required, false);
    assert.equal(await evaluate("!!document.querySelector('[name=\"password-confirmation\"]')"), false);
    record('local-first-run-admin');
    phase = 'setup_chat';
    await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
    await click('Account setup');
    await until(() => evaluate("!document.querySelector('[data-pane=agent]').inert && document.querySelector('[data-pane=work]').inert"), 'mobile account conversation');
    const atStep = step => until(() => evaluate(`document.querySelector('[data-setup-step]')?.dataset.setupStep === ${JSON.stringify(step)} && document.querySelector('[data-setup-step]').getAttribute('aria-busy') === 'false'`), 'saved setup prompt');
    await atStep('display_name');
    await fill('maipa-setup-answer', 'Synthetic onboarding owner');
    await click('Send answer');
    await atStep('workspace');
    await command('Page.reload');
    await until(() => evaluate("!![...document.querySelectorAll('button')].find(b=>b.textContent==='Account setup')"), 'resumed authenticated shell');
    await click('Account setup');
    await atStep('workspace');
    assert.ok(await evaluate("document.querySelector('[aria-label=\"Account setup conversation\"]').innerText.includes('Synthetic onboarding owner')"));
    await select('maipa-setup-answer', 'new'); await click('Send answer'); await atStep('engagement_name');
    await fill('maipa-setup-answer', 'Synthetic onboarding engagement'); await click('Send answer'); await atStep('entity_name');
    await fill('maipa-setup-answer', 'Synthetic onboarding entity'); await click('Send answer'); await atStep('currency');
    await fill('maipa-setup-answer', 'USD'); await click('Send answer'); await atStep('reporting_basis');
    await select('maipa-setup-answer', 'unknown'); await click('Send answer'); await atStep('effective_from');
    assert.match(await evaluate("document.getElementById('maipa-setup-answer').value"), /^\d{4}-\d{2}-\d{2}$/);
    await click('Send answer'); await atStep('review');
    assert.ok(await evaluate("document.querySelector('[aria-label=\"Account setup review\"]').innerText.includes('Unknown for now')"));
    const before = await evaluate("(async()=>({setup:await(await fetch('/api/onboarding/')).json(),clients:await(await fetch('/api/intake/engagements/')).json()}))()");
    assert.deepEqual(before.clients.engagements, []);
    await click('Confirm and save setup'); await atStep('complete');
    const saved = await evaluate("(async()=>{const setup=await(await fetch('/api/onboarding/')).json();return {setup,entity:await(await fetch('/api/intake/entities/'+setup.entity_id+'/')).json()};})()");
    assert.equal(saved.entity.name, 'Synthetic onboarding entity');
    assert.equal(saved.entity.facts.currency.value, 'USD');
    assert.equal(saved.entity.facts.reporting_basis.knowledge, 'unknown');
    assert.equal(saved.entity.facts.legal_form.knowledge, 'unknown');
    assert.equal(saved.setup.provider_ready, false);
    const stale = await evaluate(`(async()=>{const s=await(await fetch('/api/session/')).json();return (await fetch('/api/onboarding/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Maipa-Setup':${JSON.stringify(before.setup.setup_id)}},body:JSON.stringify({expected_revision:${before.setup.revision},action:'reply',answer:'confirm'})})).status;})()`);
    assert.equal(stale, 409);
    await click('Open saved client facts');
    await until(() => evaluate("document.querySelector('[data-pane=agent]').inert && !document.querySelector('[data-pane=work]').inert && !!document.querySelector('[data-entity-id]')"), 'confirmed client in work panel');
    assert.equal(await evaluate("document.querySelector('[data-fact-key=currency] dd').textContent"), 'USD');
    record('account-chat-resume-and-confirm');
    await click('Sign out');
    await until(() => evaluate("!!document.getElementById('maipa-username')"), 'claimed install uses sign-in');
    assert.equal(await evaluate("(async()=> (await fetch('/api/onboarding/')).status)()"), 401);
    assert.equal(await evaluate("!!document.getElementById('maipa-admin-username')"), false);
    record('first-run-stays-closed');
  } else {
  phase = "anonymous";
  await until(() => evaluate("document.body.innerText.includes('Sign in required')"), "anonymous workspace");
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
  phase = "keyboard_divider";
  const divider = await evaluate("(() => {const d=document.querySelector('[role=separator]'); d.focus(); return +d.getAttribute('aria-valuenow');})()");
  await command("Input.dispatchKeyEvent", { type: "keyDown", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 });
  await command("Input.dispatchKeyEvent", { type: "keyUp", key: "ArrowRight", code: "ArrowRight", windowsVirtualKeyCode: 39 });
  await until(async () => (await evaluate("+document.querySelector('[role=separator]').getAttribute('aria-valuenow')")) > divider, "keyboard divider");
  record("keyboard-divider");
  phase = "mobile_panels";
  await command("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await until(() => evaluate("document.querySelector('[data-pane=agent]').inert && !document.querySelector('[data-pane=work]').inert"), "mobile work area");
  await click("Conversation");
  await until(() => evaluate("!document.querySelector('[data-pane=agent]').inert && document.querySelector('[data-pane=work]').inert"), "mobile conversation");
  record("mobile-panels");
  phase = "login";
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await until(() => evaluate("!document.querySelector('[data-pane=work]').inert"), "desktop work panel active");
  async function login(username) {
    await fill("maipa-username", username);
    await fill("maipa-password", fixture.password);
    await click("Sign in");
    await until(() => evaluate("document.body.innerText.includes('Signed in')"), "real authenticated workspace");
    if (await evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')")) await click("Got it");
  }
  await fill("maipa-username", fixture.username);
  await fill("maipa-password", "synthetic-incorrect-password");
  await click("Sign in");
  await until(() => evaluate("document.querySelector('.auth-message')?.textContent.includes('could not sign you in')"), "generic failed login");
  assert.equal(await evaluate("document.getElementById('maipa-password').value"), "");
  await command("Page.reload");
  await until(() => evaluate("!!document.getElementById('maipa-username')"), "login form");
  await login(fixture.username);
  const workspace = await evaluate("(async()=>{const r=await fetch('/api/workspace/');return {status:r.status,body:await r.json()};})()");
  assert.equal(workspace.status, 200); assert.equal(workspace.body.role, "preparer");
  assert.ok(workspace.body.permissions.includes("agent.transport.check"));
  record("real-login-and-server-role");
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
  assert.equal(await evaluate("[...document.querySelectorAll('button')].find(b=>b.textContent==='Save fact revision').disabled"), true);
  const actual = await evaluate(`(async()=> (await fetch('/api/intake/entities/'+${JSON.stringify(clientEntityId)}+'/')).json())()`);
  assert.equal(actual.revision, 4); assert.equal(actual.facts.legal_form.value, "corporation");
  await click("Reload current record");
  await until(() => evaluate("document.querySelector('[data-entity-revision]')?.dataset.entityRevision === '4'"), "honest conflict recovery");
  assert.equal(await evaluate("document.querySelector('[data-fact-key=legal_form] dd').textContent"), "Corporation");
  record("intake-history-and-conflicts");
  phase = "logout_viewer";
  await click("Sign out");
  await until(() => evaluate("!!document.getElementById('maipa-username')"), "logged-out password form");
  assert.equal(await evaluate("(async()=> (await fetch('/api/session/')).status)()"), 401);
  if (await evaluate("!!document.querySelector('[aria-label=\"What\\'s new in Maipa\"]')")) await click("Got it");
  await login(fixture.viewer_username);
  const viewer = await evaluate(`(async()=>{const s=await (await fetch('/api/session/')).json();
    const role=await (await fetch('/api/workspace/?role=administrator')).json();
    const denied=await fetch('/api/agent/accounts/'+${JSON.stringify(fixture.foreign_account_id)}+'/transport-checks/',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':s.csrf_token,'X-Role':'administrator'},body:'{}'});
    const history=await (await fetch('/api/agent/accounts/'+${JSON.stringify(fixture.foreign_account_id)}+'/transport-checks/')).json();
    const foreign=await fetch('/api/agent/accounts/'+${JSON.stringify(fixture.account_id)}+'/transport-checks/');
    return {role:role.role,denied:denied.status,error:await denied.json(),history,foreign:foreign.status};})()`);
  assert.equal(viewer.role, "viewer"); assert.equal(viewer.denied, 403);
  assert.deepEqual(viewer.error, { error: { code: "permission_denied" } });
  assert.deepEqual(viewer.history, { checks: [] }); assert.equal(viewer.foreign, 404);
  record("logout-and-viewer-denial");
  phase = "intake_scope";
  const privateIntake = await evaluate(`(async()=>{
    const listed=await(await fetch('/api/intake/engagements/?role=administrator&tenant_id=foreign')).json();
    const entity=await fetch('/api/intake/entities/'+${JSON.stringify(clientEntityId)}+'/');
    const history=await fetch('/api/intake/entities/'+${JSON.stringify(clientEntityId)}+'/facts/legal_form/history/');
    return {listed,entity:entity.status,history:history.status};})()`);
  assert.deepEqual(privateIntake.listed, {engagements:[]});
  assert.equal(privateIntake.entity, 404); assert.equal(privateIntake.history, 404);
  assert.equal(await evaluate("document.body.innerText.includes('Synthetic browser entity') || document.body.innerText.includes('synthetic-browser-source-1')"), false);
  record("intake-user-isolation");
  }
  phase = "browser_exceptions";
  assert.equal(exceptions.length, 0, "No uncaught browser exceptions");
  record("no-uncaught-exceptions");
  evidence = { checks: passed.length, scenarios: passed, build_id: fixture.build_id, browser: version.Browser, cleanup: false };
} catch (error) {
  failedPhase = phase;
  if (phase === "browser_launch") {
    startupEvidence = { startup_state: launchState };
    if (Number.isInteger(launchExit) && launchExit >= -(2 ** 31) && launchExit < 2 ** 32) startupEvidence.launch_exit = launchExit;
    if (["ENOENT", "EACCES", "EPERM", "EINVAL", "ENOMEM", "UNKNOWN"].includes(launchError?.code)) startupEvidence.launch_error = launchError.code;
  }
  throw error;
} finally {
  phase = "cleanup";
  socket?.close();
  for (const entry of pending.values()) { clearTimeout(entry.timer); entry.reject(Error("Fixture ended")); }
  if (child.pid && !closed) {
    // Only the newly spawned test browser's process tree is targeted.
    spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], { windowsHide: true, stdio: "ignore", timeout: 10_000 });
    await until(() => closed, "browser exit");
  }
  assert.ok(closed || launchError, "Owned browser process exited");
  if (debugOrigin) {
    let reachable = false;
    try { await fetch(`${debugOrigin}/json/version`, { signal: AbortSignal.timeout(1000) }); reachable = true; } catch {}
    assert.equal(reachable, false, "Owned debugging endpoint closed");
  }
  assert.equal(dirname(realpathSync.native(profile)), parent);
  rmSync(profile, { recursive: true, maxRetries: 5, retryDelay: 200 });
  assert.equal(existsSync(profile), false);
  cleanupVerified = true;
  if (evidence) { record("owned-browser-cleanup"); evidence.checks = passed.length; evidence.cleanup = true; }
}
console.log(JSON.stringify(evidence));
