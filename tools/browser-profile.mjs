import assert from "node:assert/strict";
import { readFileSync, realpathSync, statSync } from "node:fs";
import { basename, dirname } from "node:path";
import { tmpdir } from "node:os";

export function ownedProfileRoot(profileRoot, temporaryRoot = tmpdir()) {
  const parent = realpathSync.native(profileRoot);
  const actual = statSync(dirname(parent), { bigint: true });
  const expected = statSync(temporaryRoot, { bigint: true });
  assert.ok(statSync(parent).isDirectory());
  assert.equal(actual.dev, expected.dev);
  assert.equal(actual.ino, expected.ino);
  assert.match(basename(parent), /^maipa-browser-fixture-[a-zA-Z0-9_-]+$/);
  return parent;
}

export function debuggingPort(contents) {
  if (typeof contents !== 'string' || contents.length > 128) return null;
  const lines = contents.trim().split(/\r?\n/);
  if (lines.length !== 2) return null;
  const [port, browserPath] = lines;
  if (!/^[1-9]\d{0,4}$/.test(port) || +port > 65535 ||
      !/^\/devtools\/browser\/[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}$/i.test(browserPath)) return null;
  return { port, browserPath };
}

/** An existing Windows file can still be locked or replaced during its write. */
export function readDebuggingPortFile(path, reader = readFileSync) {
  try { return debuggingPort(reader(path, 'utf8')); }
  catch (error) {
    if (['ENOENT', 'EBUSY', 'EPERM', 'EACCES'].includes(error?.code)) return null;
    throw error;
  }
}
