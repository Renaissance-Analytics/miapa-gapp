// Owned native-browser fixture only. No installed driver, arbitrary debug URL or human profile.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync } from "node:fs";
import { join, dirname } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { ownedProfileRoot } from "./browser-profile.mjs";

let phase = "fixture_input", failedPhase, cleanupVerified = false;
process.on("uncaughtException", () => {
  console.log(JSON.stringify({ checks: 0, reason: failedPhase || phase, cleanup: cleanupVerified }));
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
child.on("exit", () => { closed = true; });
let launchError;
child.on("error", error => { launchError = error; });
let debugOrigin, socket, evidence;
const pending = new Map();
let sequence = 0;
const exceptions = [];
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
    pending.set(id, { resolve, reject, timer });
    socket.send(JSON.stringify({ id, method, params }));
  });
}
async function click(label) {
  const point = await evaluate(`(() => { const b = [...document.querySelectorAll('button')].find(b => (b.getAttribute('aria-label') || b.textContent.trim()) === ${JSON.stringify(label)}); if (!b || b.disabled) return null; const r=b.getBoundingClientRect(); return r.width && r.height ? {x:r.x+r.width/2,y:r.y+r.height/2} : null; })()`);
  assert.ok(point, `Visible button ${label}`);
  await command("Input.dispatchMouseEvent", { type: "mousePressed", button: "left", clickCount: 1, ...point });
  await command("Input.dispatchMouseEvent", { type: "mouseReleased", button: "left", clickCount: 1, ...point });
}
try {
  await until(() => {
    if (launchError || closed) throw Error("Native browser launch failed");
    return existsSync(join(profile, "DevToolsActivePort"));
  }, "native browser");
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
    if (message.id && pending.has(message.id)) {
      const entry = pending.get(message.id); pending.delete(message.id); clearTimeout(entry.timer);
      if (message.error) entry.reject(Error("CDP command rejected")); else entry.resolve(message.result);
    }
  });
  await command("Page.enable"); await command("Runtime.enable"); await command("Network.enable");
  await command("Emulation.setDeviceMetricsOverride", { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
  await command("Page.navigate", { url: fixture.origin });
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
  async function fill(id, value) {
    assert.equal(await evaluate(`(() => { const field=document.getElementById(${JSON.stringify(id)}); if(!field || field.tagName!=='INPUT')return false; field.focus(); return document.activeElement===field; })()`), true);
    await command("Input.insertText", { text: value });
  }
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
  phase = "browser_exceptions";
  assert.equal(exceptions.length, 0, "No uncaught browser exceptions");
  record("no-uncaught-exceptions");
  evidence = { checks: passed.length, scenarios: passed, build_id: fixture.build_id, browser: version.Browser, cleanup: false };
} catch (error) {
  failedPhase = phase;
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
