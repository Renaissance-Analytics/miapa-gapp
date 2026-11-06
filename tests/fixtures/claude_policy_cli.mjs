// Protocol-only synthetic CLI. Arguments and data are not native Claude proof.
import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
const scenario = process.argv[2];
const args = process.argv.slice(3);
assert.ok(args.includes('--strict-mcp-config'));
assert.ok(args.includes('--tools'));
assert.equal(args[args.indexOf('--tools') + 1], '');
assert.ok(args.includes('--permission-mode'));
assert.equal(args[args.indexOf('--permission-mode') + 1], 'dontAsk');
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
for await (const line of createInterface({ input: process.stdin })) {
  const frame = JSON.parse(line);
  if (frame.type === 'control_request' && frame.request.subtype === 'initialize') {
    send({ type: 'control_response', response: { subtype: 'success', request_id: frame.request_id, response: {
      claude_code_version: scenario === 'wrong-version' ? '0.0.0' : '2.1.311',
      models: scenario === 'empty-models' ? [] : [{ value: 'default', displayName: 'Synthetic model' }],
    } } });
  } else if (frame.type === 'user') {
    send({ type: 'system', subtype: 'init', session_id: '00000000-0000-4000-8000-000000000033',
      claude_code_version: '2.1.311', tools: scenario === 'native-tool' ? ['Bash'] : [], mcp_servers: [] });
    if (scenario !== 'native-tool') send({ type: 'result', subtype: 'success', is_error: false,
      session_id: '00000000-0000-4000-8000-000000000033', result: 'Synthetic completed turn' });
  } else throw Error('Unexpected synthetic command');
}
