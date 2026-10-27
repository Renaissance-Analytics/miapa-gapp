"""Finite synthetic launcher owning real installation/profile locks and an isolated job."""
import asyncio
import json
from pathlib import Path
import shutil
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from backend.maipa.acp import Scope
from backend.maipa.storage import install_lock
from backend.maipa.windows_sandbox import WindowsSandbox

async def run():
    data,profile,base=map(Path,sys.argv[1:4]);node=sys.argv[4]
    with install_lock(data),install_lock(profile):
        for name in ('code','supervisor','workspace'):(base/name).mkdir(parents=True)
        code,work=base/'code',base/'workspace'
        shutil.copyfile(Path(node).resolve(strict=True),code/'node.exe')
        shutil.copyfile(Path(__file__).with_name('isolation_worker.mjs'),code/'worker.mjs')
        scope=Scope('synthetic-restart-user','synthetic-restart-account')
        with WindowsSandbox(scope,profile,writable=(work,profile/'home'),readonly=(code,base/'supervisor'),installation_id='synthetic-restart-install') as sandbox:
            child=await sandbox.spawn([str(code/'node.exe'),'--preserve-symlinks','--preserve-symlinks-main',str(code/'worker.mjs'),'hang'],
                scope=scope,cwd=work,env={},limit=4096)
            observed=json.loads(await child.stdout.readline())
            print(json.dumps({'root_pid':child.pid,'descendant_pid':observed['descendant_pid'],'profile_name':sandbox.profile_name}),flush=True)
            await child.wait()
asyncio.run(asyncio.wait_for(run(),25))
