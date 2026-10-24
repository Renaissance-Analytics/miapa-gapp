import { createHash } from "node:crypto";
import { existsSync, lstatSync, readFileSync, readdirSync, realpathSync } from "node:fs";
import { extname, join, relative } from "node:path";

const text = path => readFileSync(path, "utf8").replace(/\r\n?/g, "\n");
const ignored = new Set(["__pycache__", "node_modules", ".venv", "runtime", "artifacts", "dist", ".test-build", ".git"]);
const extensions = new Set([".py", ".ts", ".tsx", ".css", ".json", ".mjs"]);

export function createBuildInfo(root) {
  const base = realpathSync(root);
  let releases;
  try {
    for (const path of ["frontend", "frontend/src", "frontend/src/releases.json"]) {
      if (lstatSync(join(base, path)).isSymbolicLink()) throw Error();
    }
    releases = JSON.parse(text(join(base, "frontend/src/releases.json")));
    const seen = new Set();
    if (!Array.isArray(releases) || !releases.length || releases.length > 20) throw Error();
    for (const group of releases) {
      if (!group || typeof group.id !== "string" || !/^[a-z0-9][a-z0-9._-]{0,79}$/.test(group.id) || seen.has(group.id) ||
          typeof group.label !== "string" || !group.label.trim() || group.label.length > 80 ||
          !Array.isArray(group.cards) || !group.cards.length || group.cards.length > 10) throw Error();
      seen.add(group.id);
      for (const card of group.cards) {
        if (!card || typeof card.title !== "string" || !card.title.trim() || card.title.length > 120 ||
            (card.description !== undefined && (typeof card.description !== "string" || card.description.length > 600))) throw Error();
      }
    }
  } catch { throw new Error("Invalid release metadata"); }
  const files = [];
  function visit(path) {
    const info = lstatSync(path);
    if (info.isSymbolicLink()) throw new Error("Refusing linked build source");
    if (info.isDirectory()) {
      for (const item of readdirSync(path).sort()) if (!ignored.has(item) && !item.startsWith(".")) visit(join(path, item));
    } else if (info.isFile() && extensions.has(extname(path))) files.push(path);
  }
  for (const directory of ["backend", "frontend/src", "frontend/tools"]) visit(join(base, directory));
  for (const file of ["requirements.txt", "frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.mjs", "frontend/tsconfig.json", "frontend/tsconfig.test.json"]) {
    const path = join(base, file);
    if (existsSync(path)) {
      if (lstatSync(path).isSymbolicLink()) throw new Error("Refusing linked build source");
      files.push(path);
    }
  }
  const inputs = files.map(path => [relative(base, path).replaceAll("\\", "/"), text(path)]).sort(([a], [b]) => a.localeCompare(b, "en"));
  return { schema: 1, build_id: createHash("sha256").update(JSON.stringify(inputs)).digest("hex"),
    release_id: releases[0].id, release_label: releases[0].label };
}
