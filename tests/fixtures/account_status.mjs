// Link the exact app module to a finite synthetic process. No provider or credentials.
import { EventEmitter } from 'node:events';
import { readFile } from 'node:fs/promises';
import { PassThrough } from 'node:stream';
import { SourceTextModule, SyntheticModule } from 'node:vm';

const scenario = JSON.parse(process.argv[2]);
let stopCalls = 0;
let privateInputValid = false;
const provider = new SyntheticModule(['launchProvider'], function () {
  this.setExport('launchProvider', (_binary, args) => {
    if (scenario.expectedArgs && JSON.stringify(args) !== JSON.stringify(scenario.expectedArgs)) throw Error('Unexpected provider command');
    const child = new EventEmitter();
    const stdout = new PassThrough(), stderr = new PassThrough(), stdin = new PassThrough();
    let input = '';
    stdin.on('data', chunk => { input += chunk; privateInputValid = input === scenario.authorize + '\n'; });
    let close;
    const closed = new Promise(resolve => { close = resolve; });
    const finish = code => { child.emit('exit', code); close(); };
    const process = { child, io: { stdout, stderr, stdin }, closed,
      started: Promise.resolve(),
      kill() { finish(null); },
      async stop() { stopCalls += 1; return scenario.unverified
        ? { verified: false, empty: false, survivors: 1 }
        : { verified: true, empty: true, survivors: 0 }; },
    };
    setImmediate(() => {
      stdout.write(scenario.stdout ?? '');
      stderr.write(scenario.stderr ?? '');
      if (!scenario.login) finish(scenario.code);
    });
    return process;
  });
});
const app = new SourceTextModule(await readFile(new URL('../../backend/agent/account.mjs', import.meta.url), 'utf8'));
await app.link(specifier => {
  if (specifier !== '@particle-academy/prism-acp') throw Error('Unexpected import');
  return provider;
});
await app.evaluate();
try {
  const controller = app.namespace.subscriptionAccount({ workspace: 'synthetic', home: 'C:/synthetic/account',
    provider: scenario.provider ?? 'codex', provider_binary: 'synthetic' }, {});
  let value;
  if (scenario.login) {
    await controller.start();
    await new Promise(resolve => setImmediate(resolve));
    value = await controller.status();
    try {
      if (scenario.authorize !== undefined) value = await controller.authorize(scenario.authorize);
    } finally { await controller.stop(); }
  } else value = await controller.status();
  console.log(JSON.stringify({ value, stopCalls, ...(scenario.authorize !== undefined ? { privateInputValid } : {}) }));
} catch (error) {
  console.log(JSON.stringify({ error: error.message, stopCalls }));
}
