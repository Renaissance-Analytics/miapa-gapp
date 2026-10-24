// Synthetic native isolation probe, never a provider or an application tool.
import { readFileSync, writeFileSync } from "node:fs";
import { spawn } from "node:child_process";
const mode = process.argv[2];
if (mode === "hang") {
  const child = spawn(process.execPath, ["-e", "setInterval(()=>{},1000)"], { detached: true, windowsHide: true, stdio: "inherit" });
  child.once("spawn", () => process.stdout.write(JSON.stringify({ descendant_pid: child.pid }) + "\n"));
  setInterval(() => {}, 1000);
} else {
  const paths = JSON.parse(process.argv[3]);
  const read = path => { try { return readFileSync(path, "utf8") === "synthetic-only"; } catch (error) { return error.code; } };
  const reads = paths.map(read);
  let own_write, code_write;
  try { writeFileSync("assigned-write.txt", "synthetic"); own_write = true; } catch (error) { own_write = error.code; }
  try { writeFileSync(paths[3], "synthetic-only"); code_write = true; } catch (error) { code_write = error.code; }
  console.log(JSON.stringify({ reads, own_write, code_write }));
  const script = "const fs=require('node:fs');console.log(JSON.stringify(JSON.parse(process.argv[1]).map(p=>{try{return fs.readFileSync(p,'utf8')==='synthetic-only'}catch(e){return e.code}})))";
  // Reuse the inherited anonymous output handle. Creating a fresh global
  // named pipe inside an AppContainer is a separate provider IPC contract.
  const child = spawn(process.execPath, ["-e", script, JSON.stringify(paths)], { windowsHide: true, stdio: "inherit" });
  child.once("error", () => { process.exitCode = 1; });
  child.once("exit", code => {
    if (code !== 0) process.exitCode = 1;
    if (mode === "access-hold") setInterval(() => {}, 1000);
  });
}
