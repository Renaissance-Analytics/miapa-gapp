import assert from "node:assert/strict";
import { realpathSync, statSync } from "node:fs";
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
