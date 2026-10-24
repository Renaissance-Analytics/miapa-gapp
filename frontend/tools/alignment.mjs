import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const digest = value => createHash("sha256").update(value).digest("hex");
const text = path => readFileSync(path, "utf8").replace(/\r\n?/g, "\n");
const scenarioHash = scenario => digest(JSON.stringify(scenario, Object.keys(scenario).sort()));

// Contributor test source is parsed, never imported/executed by alignment.
export function checkFrontendAlignment(root) {
  const errors = [], discovered = new Map();
  let acceptance, reviews;
  try {
    acceptance = JSON.parse(text(join(root, "tests/acceptance.json")));
    reviews = JSON.parse(text(join(root, "tests/review.json")));
    if (acceptance.schema_version !== 1 || reviews.schema_version !== 1 || !Array.isArray(reviews.reviews)) throw Error();
  } catch { return ["Invalid or missing acceptance/review manifest"]; }
  for (const file of readdirSync(join(root, "tests")).filter(name => name.endsWith(".test.mjs")).sort()) {
    const source = text(join(root, "tests", file));
    const tree = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    if (tree.parseDiagnostics.length) errors.push(`Invalid test source: ${file}`);
    function visit(node) {
      if (ts.isCallExpression(node)) {
        if (ts.isPropertyAccessExpression(node.expression) && ["skip", "todo"].includes(node.expression.name.text)) errors.push(`Forbidden skip/todo: ${file}`);
        if (ts.isIdentifier(node.expression) && node.expression.text === "test") {
          const [name, callback] = node.arguments;
          if (node.arguments.length !== 2 || !name || !ts.isStringLiteral(name) || !callback ||
              !(ts.isArrowFunction(callback) || ts.isFunctionExpression(callback))) {
            errors.push(`Unsupported dynamic/options test: ${file}`);
          } else {
            const id = `${file}#${name.text}`;
            if (discovered.has(id)) errors.push(`Duplicate test: ${id}`);
            discovered.set(id, digest(source));
            let assertion = false;
            function inspect(child) {
              if (ts.isCallExpression(child) && ts.isPropertyAccessExpression(child.expression) &&
                  child.expression.expression.getText(tree) === "assert" && child.arguments.length) assertion = true;
              ts.forEachChild(child, inspect);
            }
            inspect(callback.body);
            if (!assertion) errors.push(`Missing assertion: ${id}`);
          }
        }
      }
      ts.forEachChild(node, visit);
    }
    visit(tree);
  }
  if (!discovered.size) errors.push("No tests discovered");
  if (!Array.isArray(acceptance.requirements) || !acceptance.requirements.length) return [...errors, "Nonempty requirements required"];
  const index = new Map();
  for (const review of reviews.reviews) {
    if (!review || typeof review.scenario !== "string" || typeof review.test !== "string") { errors.push("Invalid review"); continue; }
    const key = JSON.stringify([review.scenario, review.test]);
    if (index.has(key)) errors.push(`Duplicate review: ${key}`);
    index.set(key, review);
  }
  const mapped = new Set(), ids = new Set(), scenarioIds = new Set(), used = new Set();
  for (const requirement of acceptance.requirements) {
    if (!requirement || typeof requirement.id !== "string" || !requirement.id || typeof requirement.risk !== "string" || !requirement.risk) { errors.push("Invalid requirement"); continue; }
    if (ids.has(requirement.id)) errors.push(`Duplicate requirement: ${requirement.id}`);
    ids.add(requirement.id);
    if (!Array.isArray(requirement.scenarios)) { errors.push(`Missing scenarios: ${requirement.id}`); continue; }
    const kinds = new Set(requirement.scenarios.map(item => item?.kind));
    if (!kinds.has("positive") || !kinds.has("negative")) errors.push(`Positive/negative cases required: ${requirement.id}`);
    for (const scenario of requirement.scenarios) {
      if (!scenario || ["id", "given", "when", "then"].some(key => typeof scenario[key] !== "string" || !scenario[key]) || !Array.isArray(scenario.tests) || !scenario.tests.length) { errors.push("Invalid scenario"); continue; }
      if (scenarioIds.has(scenario.id)) errors.push(`Duplicate scenario: ${scenario.id}`);
      scenarioIds.add(scenario.id);
      for (const test of scenario.tests) {
        if (typeof test !== "string") { errors.push("Invalid test reference"); continue; }
        mapped.add(test);
        if (!discovered.has(test)) errors.push(`Test not discovered: ${test}`);
        const key = JSON.stringify([scenario.id, test]), review = index.get(key);
        if (!review) { errors.push(`Missing semantic review: ${key}`); continue; }
        used.add(key);
        if (review.source_sha256 !== discovered.get(test)) errors.push(`Review source changed: ${test}`);
        if (review.scenario_sha256 !== scenarioHash(scenario)) errors.push(`Review scenario changed: ${scenario.id}`);
        if (review.expected_observation !== scenario.then || review.decision !== "aligned" ||
            typeof review.reason !== "string" || !review.reason.trim() || typeof review.reviewer !== "string" || !review.reviewer.trim()) errors.push(`Incomplete review: ${key}`);
      }
    }
  }
  for (const id of discovered.keys()) if (!mapped.has(id)) errors.push(`Unmapped test: ${id}`);
  for (const id of index.keys()) if (!used.has(id)) errors.push(`Stale review: ${id}`);
  return errors;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const errors = checkFrontendAlignment(resolve(dirname(fileURLToPath(import.meta.url)), ".."));
  console.log(errors.length ? `Frontend alignment FAIL\n${errors.join("\n")}` : "Frontend alignment PASS; recorded semantic review still requires judgment.");
  process.exitCode = errors.length ? 1 : 0;
}
