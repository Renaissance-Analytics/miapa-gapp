import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, realpathSync, rmSync, statSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ownedProfileRoot } from "../../tools/browser-profile.mjs";

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
