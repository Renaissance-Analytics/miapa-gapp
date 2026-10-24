"""Check the actual published host without creating any provider session."""
import asyncio
import json
from pathlib import Path
import shutil

from django.conf import settings

from backend.maipa.acp import AcpClient, AcpError, Scope
from backend.maipa.worker_capsule import WorkerCapsule
from backend.maipa.windows_sandbox import WindowsSandbox
from backend.maipa.workspace.models import Membership
from .models import AgentAccount

PACKAGE = "@particle-academy/prism-acp"
VERSION = "0.24.1"


class TransportUnavailable(Exception):
    """No raw filesystem, child-process or protocol diagnostic crosses HTTP."""


def _read_metadata(path):
    with path.open("rb") as handle:
        data = handle.read(4097)
    if len(data) > 4096:
        raise ValueError("Metadata budget")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Metadata shape")
    return value


def installed_runtime():
    try:
        root = Path(settings.ACP_RUNTIME_ROOT)
        manifest = _read_metadata(root / "node_modules/@particle-academy/prism-acp/package.json")
        lock = _read_metadata(root / "package-lock.json")
        packages = lock.get("packages")
        if (manifest.get("name") != PACKAGE or manifest.get("version") != VERSION or
                manifest.get("dependencies") or manifest.get("peerDependencies") or
                not isinstance(packages, dict) or set(packages) != {"", "node_modules/" + PACKAGE} or
                packages["node_modules/" + PACKAGE].get("version") != VERSION):
            raise ValueError("Unexpected runtime")
        return {"package": PACKAGE, "version": VERSION, "provider_sessions_enabled": False}
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        raise TransportUnavailable("Agent runtime unavailable") from None


async def _check(command, profile, launcher):
    scope = Scope(profile["user_id"], profile["account_id"])
    client = AcpClient(command, scope, Path(profile["workspace"]),
        env={"MAIPA_ACP_PROFILE": json.dumps(profile)}, timeout=15, launcher=launcher)
    await client.start()
    try:
        result = await client.initialize(scope)
        if (result.get("agentInfo", {}).get("name") != "maipa-prism-acp" or
                result["agentInfo"].get("version") != VERSION or result.get("authMethods") != []):
            raise ValueError("Unexpected initialization")
        verdict = await client.shutdown(scope)
        if verdict != {"verified": True, "survivors": 0, "sessions": 0}:
            raise ValueError("Unexpected provider process")
    finally:
        await client.close()
    if client.returncode != 0:
        raise ValueError("Host exit")
    if launcher.process.isolation != "windows-appcontainer" or launcher.process.job_empty is not True:
        raise ValueError("Worker stop unverified")
    return {"isolation": "windows-appcontainer", "job_empty": True, "provider_sessions": 0}


def _authority(account):
    # Preserve the original caller's immutable owner/provider binding. Refreshing
    # an object and silently adopting a newly assigned owner would be escalation.
    exists = AgentAccount.objects.filter(pk=account.pk, owner_id=account.owner_id,
        provider=account.provider, enabled=True, owner__is_active=True).exists()
    member = Membership.objects.filter(user_id=account.owner_id, enabled=True,
        role__in=("administrator", "preparer"), installation__slot=1).first()
    if not exists or member is None:
        raise TransportUnavailable("Agent transport unavailable")
    return str(member.installation_id)


def probe_transport(account):
    installed_runtime()
    try:
        installation = _authority(account)
        node = settings.ACP_NODE_EXECUTABLE or shutil.which("node")
        if not node or not Path(node).is_absolute():
            raise ValueError("Node unavailable")
        scope = Scope(str(account.owner_id), str(account.pk))
        with WorkerCapsule(settings.ACP_CHECK_ROOT, settings.ACP_RUNTIME_ROOT, node, scope, account.provider) as capsule:
            # Recheck after preparation and before creating any worker process.
            if _authority(account) != installation:
                raise TransportUnavailable("Agent transport unavailable")
            with WindowsSandbox(scope, capsule.base, writable=(capsule.workspace, capsule.home),
                    readonly=(capsule.code, capsule.supervisor), installation_id=installation) as sandbox:
                verdict = asyncio.run(_check(capsule.command, capsule.profile, sandbox))
            if not sandbox.profile_removed or _authority(account) != installation:
                raise TransportUnavailable("Agent transport unavailable")
        return verdict
    except (OSError, ValueError, RuntimeError, AcpError, AttributeError, TypeError, RecursionError):
        raise TransportUnavailable("Agent transport unavailable") from None
