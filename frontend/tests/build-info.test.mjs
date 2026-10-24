import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, realpathSync } from "node:fs";
import { join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { createBuildInfo } from "../tools/build-info.mjs";

function fixture() {
  const parent = realpathSync(tmpdir());
  const root = mkdtempSync(join(parent, "maipa-build-fixture-"));
  for (const path of ["backend", "frontend/src", "frontend/tools", "runtime"]) mkdirSync(join(root, path), { recursive: true });
  writeFileSync(join(root, "backend/api.py"), "status = 'ok'\n");
  writeFileSync(join(root, "frontend/src/App.tsx"), "export const shell = 'Maipa';\n");
  writeFileSync(join(root, "frontend/src/releases.json"), JSON.stringify([{ id: "development-2", label: "Development update 2", cards: [{ title: "Track app updates", description: "See changes." }] }]));
  writeFileSync(join(root, "runtime/private.json"), "synthetic private value");
  return { root, close() {
    const verified = realpathSync(root);
    if (resolve(verified, "..") !== parent || !verified.startsWith(join(parent, "maipa-build-fixture-"))) throw Error("Refusing unsafe fixture cleanup");
    rmSync(verified, { recursive: true });
  } };
}

test("build identity is stable across repeated builds and checkout newlines but changes with backend frontend or notes", () => {
  const f = fixture();
  try {
    const first = createBuildInfo(f.root);
    assert.match(first.build_id, /^[a-f0-9]{64}$/);
    assert.deepEqual(first, createBuildInfo(f.root));
    writeFileSync(join(f.root, "backend/api.py"), "status = 'ok'\r\n");
    assert.deepEqual(first, createBuildInfo(f.root));
    writeFileSync(join(f.root, "runtime/private.json"), "changed private value");
    assert.deepEqual(first, createBuildInfo(f.root));
    writeFileSync(join(f.root, "backend/api.py"), "status = 'changed'\n");
    const backend = createBuildInfo(f.root);
    assert.notEqual(first.build_id, backend.build_id);
    writeFileSync(join(f.root, "frontend/src/App.tsx"), "export const shell = 'changed';\n");
    const frontend = createBuildInfo(f.root);
    assert.notEqual(backend.build_id, frontend.build_id);
    writeFileSync(join(f.root, "frontend/src/releases.json"), JSON.stringify([{ id: "development-3", label: "Development update 3", cards: [{ title: "New notes" }] }]));
    assert.notEqual(frontend.build_id, createBuildInfo(f.root).build_id);
    assert.equal(first.release_id, "development-2");
  } finally { f.close(); }
});

test("empty duplicate or malformed release metadata stops the build instead of generating a misleading release", () => {
  const f = fixture();
  try {
    for (const content of ["[]", "invalid-json", JSON.stringify([{ id: "duplicate", label: "One", cards: [{ title: "One" }] }, { id: "duplicate", label: "Two", cards: [{ title: "Two" }] }]), JSON.stringify([{ id: "bad", label: "Bad", cards: [{ title: 42 }] }])]) {
      writeFileSync(join(f.root, "frontend/src/releases.json"), content);
      assert.throws(() => createBuildInfo(f.root), /Invalid release metadata/);
    }
  } finally { f.close(); }
});
