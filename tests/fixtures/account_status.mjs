// Link the exact app module to a finite synthetic process. No provider or credentials.
import { EventEmitter } from 'node:events';
import { readFile } from 'node:fs/promises';
import { PassThrough } from 'node:stream';
import { SourceTextModule, SyntheticModule } from 'node:vm';

const scenario = JSON.parse(process.argv[2]);
let stopCalls = 0;
const provider = new SyntheticModule(['launchProvider'], function () {
  this.setExport('launchProvider', () => {
    const child = new EventEmitter();
    const stdout = new PassThrough(), stderr = new PassThrough();
    let close;
    const closed = new Promise(resolve => { close = resolve; });
    const finish = code => { child.emit('exit', code); close(); };
    const process = { child, io: { stdout, stderr }, closed,
      started: Promise.resolve(),
      kill() { finish(null); },
      async stop() { stopCalls += 1; return scenario.unverified
        ? { verified: false, empty: false, survivors: 1 }
        : { verified: true, empty: true, survivors: 0 }; },
    };
    setImmediate(() => {
      stdout.write(scenario.stdout ?? '');
      stderr.write(scenario.stderr ?? '');
      finish(scenario.code);
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
  const value = await app.namespace.subscriptionAccount({ workspace: 'synthetic', provider_binary: 'synthetic' }, {}).status();
  console.log(JSON.stringify({ value, stopCalls }));
} catch (error) {
  console.log(JSON.stringify({ error: error.message, stopCalls }));
}
