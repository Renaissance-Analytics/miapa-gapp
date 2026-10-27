// Application-owned subscription login controller using Prism's public process lifecycle.
import { launchProvider } from '@particle-academy/prism-acp';

const DEVICE_URL = 'https://auth.openai.com/codex/device';
const binaryArgs = ['-c', 'forced_login_method="chatgpt"', '-c', 'cli_auth_credentials_store="file"'];
export function claudeLoginUrl(value) {
  try {
    if (typeof value !== 'string' || value.length > 2048 || /[\x00-\x20\x7f]/.test(value)) return false;
    const url = new URL(value);
    const route = (url.hostname === 'claude.com' && url.pathname === '/cai/oauth/authorize')
      || (url.hostname === 'claude.ai' && url.pathname === '/oauth/authorize');
    return url.protocol === 'https:' && route && !url.port && !url.username && !url.password && !url.hash;
  } catch { return false; }
}
export function subscriptionAccount(profile, env) {
  if (!['codex', 'claude'].includes(profile.provider)) throw Error('Account unavailable');
  const claude = profile.provider === 'claude';
  let login, consent, loginClosed = false, submitted = false, timer;
  const options = { cwd: profile.workspace, env, windowsStdio: 'local-pipes', startupTimeoutMs: 10000, ioTimeoutMs: 30000 };
  async function status() {
    if (login && !loginClosed) return { state: 'sign_in', ...(consent ? { login: consent } : {}) };
    const process = launchProvider(profile.provider_binary, claude ? ['auth', 'status'] : [...binaryArgs, 'login', 'status'], options);
    let text = '', errors = '', bytes = 0;
    const read = (chunk, stderr = false) => {
      bytes += chunk.length;
      if (bytes > 16384) { process.kill(); return; }
      if (claude && stderr) errors += chunk.toString('utf8');
      else text += chunk.toString('utf8');
    };
    const io = process.io ?? process.child;
    io.stdout.on('data', read); io.stderr.on('data', chunk => read(chunk, true));
    const timeout = setTimeout(() => process.kill(), 15000);
    try {
      const exited = new Promise(resolve => process.child.once('exit', resolve));
      await process.started;
      const code = await exited;
      await process.closed;
      const stop = await process.stop({ graceMs: 0 });
      if (!stop.verified || !stop.empty || stop.survivors !== 0 || bytes > 16384) throw Error('Account unavailable');
      if (claude) {
        let result;
        try { result = JSON.parse(text); } catch { throw Error('Account unavailable'); }
        const normalize = path => typeof path === 'string' ? path.replaceAll('\\', '/').replace(/\/+$/, '').toLowerCase() : null;
        if (!result || Array.isArray(result) || errors.trim() || !profile.home
            || normalize(result.configDirectory) !== normalize(profile.home)) throw Error('Account unavailable');
        if (code === 0 && result.loggedIn === true && result.authMethod === 'claude.ai') return { state: 'ready' };
        if (code === 1 && result.loggedIn === false && result.authMethod === 'none') return { state: 'sign_in' };
        throw Error('Account unavailable');
      }
      // Only the pinned adapter's confirmed signed-out result requests consent.
      // Configuration/process errors and key-based logins are not subscription readiness.
      if (code === 0 && text.trim() === 'Logged in using ChatGPT') return { state: 'ready' };
      if (code === 1 && text.trim() === 'Not logged in') return { state: 'sign_in' };
      throw Error('Account unavailable');
    } finally { clearTimeout(timeout); await process.stop({ graceMs: 0 }); }
  }
  async function start() {
    if (login && !loginClosed) return status();
    consent = undefined; loginClosed = false; submitted = false;
    login = launchProvider(profile.provider_binary, claude ? ['auth', 'login', '--claudeai'] : [...binaryArgs, 'login', '--device-auth'], options);
    let text = '', bytes = 0;
    const io = login.io ?? login.child;
    const read = chunk => {
      bytes += chunk.length;
      if (bytes > 16384) { login.kill(); return; }
      text += chunk.toString('utf8').replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '');
      if (claude) {
        const url = text.match(/https:\/\/[^\s<>"']+/g)?.find(claudeLoginUrl);
        if (url && !submitted) consent = { url, accepts_code: true };
      } else {
        const code = text.match(/\b([A-Z0-9]{4,8}-[A-Z0-9]{4,8})\b/);
        if (code && text.includes(DEVICE_URL)) consent = { url: DEVICE_URL, code: code[1] };
      }
    };
    io.stdout.on('data', read); io.stderr.on('data', read);
    login.closed.then(() => { loginClosed = true; consent = undefined; clearTimeout(timer); }).catch(() => { loginClosed = true; consent = undefined; });
    timer = setTimeout(() => login.kill(), 600000);
    await login.started;
    return { state: 'sign_in' };
  }
  async function authorize(code) {
    if (!claude || !login || loginClosed || !consent?.accepts_code || typeof code !== 'string'
        || !code.trim() || code.length > 2048 || /[\x00-\x20\x7f]/.test(code)) throw Error('Account unavailable');
    const io = login.io ?? login.child;
    consent = undefined; submitted = true;
    // Private native stdin only: never a command argument, log or model prompt.
    if (!io.stdin?.writable || !io.stdin.write(code + '\n')) throw Error('Account unavailable');
    return { state: 'sign_in' };
  }
  async function stop() {
    clearTimeout(timer); consent = undefined;
    if (!login) return { verified: true, empty: true, survivors: 0 };
    const result = await login.stop({ graceMs: 0 }); login = undefined; return result;
  }
  return { status, start, authorize, stop };
}

export { binaryArgs as subscriptionArgs };
