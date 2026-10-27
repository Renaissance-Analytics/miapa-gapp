// Application-owned subscription login controller using Prism's public process lifecycle.
import { launchProvider } from '@particle-academy/prism-acp';

const URL = 'https://auth.openai.com/codex/device';
const binaryArgs = ['-c', 'forced_login_method="chatgpt"', '-c', 'cli_auth_credentials_store="file"'];
export function subscriptionAccount(profile, env) {
  let login, consent, loginClosed = false, loginCode, timer;
  const options = { cwd: profile.workspace, env, windowsStdio: 'local-pipes', startupTimeoutMs: 10000, ioTimeoutMs: 30000 };
  async function status() {
    if (login && !loginClosed) return { state: 'sign_in', ...(consent ? { login: consent } : {}) };
    const process = launchProvider(profile.provider_binary, [...binaryArgs, 'login', 'status'], options);
    let text = '', bytes = 0;
    const read = chunk => {
      bytes += chunk.length;
      if (bytes > 16384) { process.kill(); return; }
      text += chunk.toString('utf8');
    };
    const io = process.io ?? process.child;
    io.stdout.on('data', read); io.stderr.on('data', read);
    const timeout = setTimeout(() => process.kill(), 15000);
    try {
      const exited = new Promise(resolve => process.child.once('exit', resolve));
      await process.started;
      const code = await exited;
      await process.closed;
      const stop = await process.stop({ graceMs: 0 });
      if (!stop.verified || !stop.empty || stop.survivors !== 0 || bytes > 16384) throw Error('Account unavailable');
      // Only the pinned adapter's confirmed signed-out result requests consent.
      // Configuration/process errors and key-based logins are not subscription readiness.
      if (code === 0 && text.trim() === 'Logged in using ChatGPT') return { state: 'ready' };
      if (code === 1 && text.trim() === 'Not logged in') return { state: 'sign_in' };
      throw Error('Account unavailable');
    } finally { clearTimeout(timeout); await process.stop({ graceMs: 0 }); }
  }
  async function start() {
    if (login && !loginClosed) return status();
    consent = undefined; loginCode = undefined; loginClosed = false;
    login = launchProvider(profile.provider_binary, [...binaryArgs, 'login', '--device-auth'], options);
    let text = '', bytes = 0;
    const io = login.io ?? login.child;
    const read = chunk => {
      bytes += chunk.length;
      if (bytes > 16384) { login.kill(); return; }
      text += chunk.toString('utf8').replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '');
      const code = text.match(/\b([A-Z0-9]{4,8}-[A-Z0-9]{4,8})\b/);
      if (code && text.includes(URL)) consent = { url: URL, code: code[1] };
    };
    io.stdout.on('data', read); io.stderr.on('data', read);
    login.child.once('exit', code => { loginCode = code; });
    login.closed.then(() => { loginClosed = true; consent = undefined; clearTimeout(timer); }).catch(() => { loginClosed = true; consent = undefined; });
    timer = setTimeout(() => login.kill(), 600000);
    await login.started;
    return { state: 'sign_in' };
  }
  async function stop() {
    clearTimeout(timer); consent = undefined;
    if (!login) return { verified: true, empty: true, survivors: 0 };
    const result = await login.stop({ graceMs: 0 }); login = undefined; return result;
  }
  return { status, start, stop };
}

export { binaryArgs as subscriptionArgs };
