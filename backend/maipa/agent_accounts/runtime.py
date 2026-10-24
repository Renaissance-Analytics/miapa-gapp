"""Check the actual published host without creating any provider session."""
import asyncio
import json
from pathlib import Path
import shutil
import tempfile

from django.conf import settings

from backend.maipa.acp import AcpClient, AcpError, Scope

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


async def _check(command, profile):
    scope = Scope(profile["user_id"], profile["account_id"])
    client = AcpClient(command, scope, Path(profile["workspace"]),
        env={"MAIPA_ACP_PROFILE": json.dumps(profile)}, timeout=15)
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


def probe_transport(account):
    installed_runtime()
    try:
        node = settings.ACP_NODE_EXECUTABLE or shutil.which("node")
        if not node or not Path(node).is_absolute():
            raise ValueError("Node unavailable")
        root = Path(settings.ACP_CHECK_ROOT)
        if not root.is_absolute() or root.is_symlink():
            raise ValueError("Invalid check directory")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = root.resolve(strict=True)
        # Only a freshly minted, confined temporary directory is cleaned up.
        with tempfile.TemporaryDirectory(prefix="transport-", dir=root) as directory:
            base = Path(directory).resolve(strict=True)
            if base.parent != root or base.is_symlink():
                raise ValueError("Invalid temporary directory")
            for name in ("workspace", "home", "supervisor"):
                (base / name).mkdir(mode=0o700)
            profile = {"user_id": str(account.owner_id), "account_id": str(account.pk), "provider": account.provider,
                "workspace": str(base / "workspace"), "home": str(base / "home"), "supervisor_directory": str(base / "supervisor")}
            asyncio.run(_check([node, str(Path(settings.ACP_RUNTIME_ROOT) / "host.mjs")], profile))
    except (OSError, ValueError, RuntimeError, AcpError, AttributeError, TypeError, RecursionError):
        raise TransportUnavailable("Agent transport unavailable") from None
