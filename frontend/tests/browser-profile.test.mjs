import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { debuggingPort, ownedProfileRoot } from "../../tools/browser-profile.mjs";

test("browser fixture accepts its actual owned temporary parent", () => {
  const parent = realpathSync(tmpdir()), root = mkdtempSync(join(parent, "maipa-browser-fixture-"));
  try { assert.equal(statSync(ownedProfileRoot(root, parent), { bigint: true }).ino, statSync(root, { bigint: true }).ino); }
  finally { assert.equal(statSync(ownedProfileRoot(root, parent), { bigint: true }).ino, statSync(root, { bigint: true }).ino); rmSync(root, { recursive: true }); }
});

test("Windows aliases and path casing identify the same physical temporary directory", () => {
  const parent = realpathSync(tmpdir()), root = mkdtempSync(join(parent, "maipa-browser-fixture-"));
  try { assert.equal(statSync(ownedProfileRoot(root, parent.toUpperCase()), { bigint: true }).ino, statSync(root, { bigint: true }).ino); }
  finally { assert.equal(statSync(ownedProfileRoot(root, parent), { bigint: true }).ino, statSync(root, { bigint: true }).ino); rmSync(root, { recursive: true }); }
});

test("browser fixture refuses a different physical parent and unowned directory name", () => {
  const parent = realpathSync(tmpdir()), root = mkdtempSync(join(parent, "maipa-browser-fixture-"));
  const other = mkdtempSync(join(parent, "maipa-unowned-"));
  try {
    assert.throws(() => ownedProfileRoot(root, other));
    assert.throws(() => ownedProfileRoot(other, parent));
  } finally {
    assert.equal(statSync(ownedProfileRoot(root, parent), { bigint: true }).ino, statSync(root, { bigint: true }).ino);
    rmSync(root, { recursive: true });
    assert.equal(realpathSync(other).startsWith(join(parent, "maipa-unowned-")), true);
    rmSync(other, { recursive: true });
  }
});

test('browser startup tolerates transient file open races but never accepts an unread or unsafe record', async () => {
  const { readDebuggingPortFile } = await import('../../tools/browser-profile.mjs');
  const parent = realpathSync(tmpdir()), root = mkdtempSync(join(parent, 'maipa-browser-fixture-'));
  const file = join(root, 'DevToolsActivePort');
  const browserPath = '/devtools/browser/12345678-1234-4234-8234-123456789abc';
  try {
    writeFileSync(file, `43127\n${browserPath}`);
    for (const code of ['ENOENT', 'EBUSY', 'EPERM', 'EACCES']) {
      let attempts = 0;
      const reader = (path, encoding) => {
        assert.equal(path, file);
        assert.equal(encoding, 'utf8');
        if (++attempts === 1) throw Object.assign(Error('synthetic private file'), { code });
        return readFileSync(path, encoding);
      };
      assert.equal(readDebuggingPortFile(file, reader), null);
      assert.deepEqual(readDebuggingPortFile(file, reader), { port: '43127', browserPath });
      assert.equal(attempts, 2);
    }
    writeFileSync(file, `43127\nhttps://foreign.invalid/`);
    assert.equal(readDebuggingPortFile(file), null);
    assert.throws(() => readDebuggingPortFile(file, () => { throw Object.assign(Error('synthetic failure'), { code: 'EIO' }); }), { code: 'EIO' });
  } finally {
    assert.equal(statSync(ownedProfileRoot(root, parent), { bigint: true }).ino, statSync(root, { bigint: true }).ino);
    rmSync(root, { recursive: true });
  }
});

test('browser connection readiness waits through partial file writes and rejects unsafe records', () => {
  const parent = realpathSync(tmpdir()), root = mkdtempSync(join(parent, 'maipa-browser-fixture-'));
  const file = join(root, 'DevToolsActivePort');
  const path = '/devtools/browser/12345678-1234-4234-8234-123456789abc';
  try {
    for (const partial of ['', '43', '43127\n', '43127\n/devtools/browser/12345678-1234-4234']) {
      writeFileSync(file, partial);
      assert.equal(debuggingPort(readFileSync(file, 'utf8')), null);
    }
    writeFileSync(file, `43127\n${path}`);
    assert.deepEqual(debuggingPort(readFileSync(file, 'utf8')), { port: '43127', browserPath: path });
    for (const invalid of [`0\n${path}`, `70000\n${path}`, `127.0.0.1:43127\n${path}`, `43127\n${path}\nextra`, `43127\nhttps://foreign.invalid/`]) {
      assert.equal(debuggingPort(invalid), null);
    }
  } finally {
    assert.equal(statSync(ownedProfileRoot(root, parent), { bigint: true }).ino,
      statSync(root, { bigint: true }).ino);
    rmSync(root, { recursive: true });
  }
});
