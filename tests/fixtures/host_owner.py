"""Finite native host owner fixture; killed by the test to check kernel cleanup."""
import asyncio
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.maipa.acp import Scope
from backend.maipa.windows_sandbox import WindowsHostWorker

async def main():
    scope=Scope('crash-owner','crash-account')
    root=Path(sys.argv[1])
    with WindowsHostWorker(scope,root) as launcher:
        process=await launcher.spawn((shutil.which('node'),str(root/'finite.mjs')),scope=scope,cwd=root,env={},limit=65536)
        line=await asyncio.wait_for(process.stdout.readline(),10)
        print(str(process.pid)+' '+line.decode().strip(),flush=True)
        await asyncio.sleep(30)
asyncio.run(main())
