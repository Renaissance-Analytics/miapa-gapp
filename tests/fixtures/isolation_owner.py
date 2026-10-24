"""Our finite synthetic server process; its parent test kills it intentionally."""
import asyncio
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.maipa.acp import Scope
from backend.maipa.windows_sandbox import WindowsSandbox

async def main():
    base, node, fixture = Path(sys.argv[1]).resolve(), sys.argv[2], Path(sys.argv[3])
    work, code = base / "work", base / "code"
    work.mkdir()
    code.mkdir()
    shutil.copyfile(fixture, code / "worker.mjs")
    shutil.copyfile(node, code / "node.exe")
    owner = Scope("synthetic-user", "synthetic-account")
    with WindowsSandbox(owner, base, writable=(work,), readonly=(code,), installation_id="synthetic-install") as sandbox:
        child = await sandbox.spawn([str(code / "node.exe"), "--preserve-symlinks", "--preserve-symlinks-main", str(code / "worker.mjs"), "hang"], scope=owner, cwd=work, env={}, limit=4096)
        data = json.loads(await child.stdout.readline())
        print(json.dumps({"root_pid": child.pid, "descendant_pid": data["descendant_pid"], "profile_name": sandbox.profile_name}), flush=True)
        await child.wait()

asyncio.run(asyncio.wait_for(main(), timeout=25))
