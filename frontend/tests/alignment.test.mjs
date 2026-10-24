import assert from "node:assert/strict";
import test from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, realpathSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { createHash } from "node:crypto";
import { checkFrontendAlignment } from "../tools/alignment.mjs";

function fixture(run) {
  const parent = realpathSync(tmpdir());
  const root = mkdtempSync(join(parent, "maipa-alignment-"));
  mkdirSync(join(root, "tests"));
  const source = "import test from 'node:test'; import assert from 'node:assert/strict';\n" +
    "test('valid', () => { assert.equal(Number('5'),5); });\n" +
    "test('invalid', () => { assert.throws(() => JSON.parse('invalid')); });\n";
  const requirement = { id: "SAMPLE", risk: "Invalid input could be accepted", scenarios: [
    { id: "SAMPLE.valid", kind: "positive", given: "Text 5", when: "Convert to number", then: "Exactly 5", tests: ["sample.test.mjs#valid"] },
    { id: "SAMPLE.invalid", kind: "negative", given: "Invalid JSON", when: "Parse JSON", then: "Throw", tests: ["sample.test.mjs#invalid"] },
  ] };
  const digest = value => createHash("sha256").update(value).digest("hex");
  const reviews = requirement.scenarios.map(scenario => ({
    scenario: scenario.id, test: scenario.tests[0], source_sha256: digest(source),
    scenario_sha256: digest(JSON.stringify(scenario, Object.keys(scenario).sort())),
    expected_observation: scenario.then, reviewer: "fixture", reason: "Exact sum and invalid JSON exception asserted", decision: "aligned",
  }));
  writeFileSync(join(root, "tests/sample.test.mjs"), source);
  writeFileSync(join(root, "tests/acceptance.json"), JSON.stringify({ schema_version: 1, requirements: [requirement] }));
  writeFileSync(join(root, "tests/review.json"), JSON.stringify({ schema_version: 1, reviews }));
  try { run({ root, source }); } finally {
    if (dirname(realpathSync(root)) !== parent || !basename(root).startsWith("maipa-alignment-")) throw Error("Unsafe fixture cleanup target");
    rmSync(root, { recursive: true, force: true });
  }
}

test("frontend alignment accepts independent reviewed positive and negative fixtures", () => {
  fixture(({ root }) => assert.deepEqual(checkFrontendAlignment(root), []));
});

test("frontend alignment rejects missing coverage and skipped tests", () => {
  fixture(({ root, source }) => {
    writeFileSync(join(root, "tests/sample.test.mjs"), source.replace("test('invalid'", "test.skip('invalid'"));
    assert.ok(checkFrontendAlignment(root).some(error => error.includes("skip")));
    writeFileSync(join(root, "tests/acceptance.json"), JSON.stringify({ schema_version: 1, requirements: [] }));
    assert.ok(checkFrontendAlignment(root).some(error => error.includes("requirements")));
  });
});

test("frontend alignment rejects weakened tests and unrelated mappings after review", () => {
  fixture(({ root, source }) => {
    writeFileSync(join(root, "tests/sample.test.mjs"), source.replace("assert.equal(Number('5'),5)", "assert.ok(Number('5'))"));
    assert.ok(checkFrontendAlignment(root).some(error => error.includes("source changed")));
    writeFileSync(join(root, "tests/sample.test.mjs"), source + "test('unmapped', () => assert.equal(Number('7'), 7));");
    assert.ok(checkFrontendAlignment(root).some(error => error.includes("Unmapped")));
    writeFileSync(join(root, "tests/sample.test.mjs"), source);
    const acceptance = JSON.parse(readFileSync(join(root, "tests/acceptance.json"), "utf8"));
    const scenarios = acceptance.requirements[0].scenarios;
    [scenarios[0].tests, scenarios[1].tests] = [scenarios[1].tests, scenarios[0].tests];
    writeFileSync(join(root, "tests/acceptance.json"), JSON.stringify(acceptance));
    assert.ok(checkFrontendAlignment(root).some(error => error.includes("Missing semantic review")));
  });
});
