"""Temporary deployment of exact public runtime bytes, never a package patch.

Only the fixed host/manifest and installed zero-dependency Prism package are
copied. This keeps private server files outside the worker ACL and leaves the
installed package tree untouched. Prepared supervisor code stays read-only.
An optional original-account lease supplies a durable home; only the newly
created worker directory is ever removed by capsule cleanup.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .windows_sandbox import SandboxUnavailable, physical_path


class WorkerCapsule:
    def __init__(self, root, runtime, node, scope, provider, *, profile_lease=None, ui_tools=False):
        # A server-configured Node install may use a version-manager alias.
        # Resolve that trusted executable once, then validate and copy its
        # physical bytes. Runtime and writable paths never accept reparse hops.
        self.root, self.runtime, self.node = Path(root), physical_path(runtime), physical_path(Path(node).resolve(strict=True), directory=False)
        self.scope, self.provider, self.temporary = scope, provider, None
        self.profile_lease = profile_lease
        if type(ui_tools) is not bool or (ui_tools and provider != 'codex'):
            raise SandboxUnavailable('Worker UI transport unavailable')
        self.ui_tools = ui_tools
        if not self.root.is_absolute() or provider not in {"codex", "claude"}:
            raise SandboxUnavailable("Worker layout unavailable")
        self._leased_home()

    def _leased_home(self):
        if self.profile_lease is None:
            return None
        # Import only for this trusted Django consumer. Temporary zero-provider
        # capsules retain their independent stdlib-only initialization contract.
        from .agent_accounts.profiles import ProfileLease, ProfileUnavailable
        try:
            lease = self.profile_lease
            if not isinstance(lease, ProfileLease) or lease.scope != self.scope or lease.provider != self.provider:
                raise SandboxUnavailable("Worker profile unavailable")
            lease.revalidate()
            home = physical_path(lease.home)
            if home.name != 'home' or home.parent != physical_path(self.root):
                raise SandboxUnavailable("Worker profile unavailable")
            return home
        except ProfileUnavailable:
            raise SandboxUnavailable("Worker profile unavailable") from None

    def __enter__(self):
        try:
            leased_home = self._leased_home()
            for path in (self.root, *self.root.parents):
                if path.exists() or path.is_symlink():
                    physical_path(path)
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.root = physical_path(self.root)
            self.temporary = tempfile.TemporaryDirectory(prefix="worker-", dir=self.root)
            self.base = physical_path(self.temporary.name)
            if self.base.parent != self.root:
                raise SandboxUnavailable("Worker layout unavailable")
            for name in ("code", "supervisor", "workspace") + (() if leased_home else ("home",)):
                (self.base / name).mkdir(mode=0o700)
            self.code, self.supervisor, self.workspace = [self.base / name for name in ("code", "supervisor", "workspace")]
            self.home = leased_home or self.base / 'home'
            self.isolation_root = self.root if leased_home else self.base
            # The container must be able to read its own executable when it
            # launches descendants. Never change the installed Node tree ACL.
            node_bytes = self.node.read_bytes()
            if not 1 <= len(node_bytes) <= 134_217_728:
                raise SandboxUnavailable("Worker runtime budget")
            self.worker_node = self.code / "node.exe"
            self.worker_node.write_bytes(node_bytes)
            self.node_sha256 = hashlib.sha256(node_bytes).hexdigest()
            if hashlib.sha256(self.worker_node.read_bytes()).hexdigest() != self.node_sha256:
                raise SandboxUnavailable("Worker runtime unavailable")
            package = self.runtime / "node_modules/@particle-academy/prism-acp"
            physical_path(package)
            files = [self.runtime / name for name in ("host.mjs", "package.json", "package-lock.json")]
            if self.ui_tools:
                files.append(self.runtime / 'ui-mcp.mjs')
            files.extend(path for path in package.rglob("*") if not path.is_dir())
            total, self.files_sha256 = 0, {}
            for source in files:
                source = physical_path(source, directory=False)
                if not source.is_relative_to(self.runtime):
                    raise SandboxUnavailable("Worker runtime unavailable")
                relative = source.relative_to(self.runtime)
                raw = source.read_bytes()
                total += len(raw)
                if len(raw) > 2_097_152 or total > 16_777_216 or len(files) > 256:
                    raise SandboxUnavailable("Worker runtime budget")
                target = self.code / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                digest = hashlib.sha256(raw).hexdigest()
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise SandboxUnavailable("Worker runtime unavailable")
                self.files_sha256[relative.as_posix()] = digest
            entry = (self.code / "node_modules/@particle-academy/prism-acp/dist/index.js").as_uri()
            script = "import {prepareProcessTree} from " + json.dumps(entry) + "; await prepareProcessTree({directory:process.argv[1]});"
            allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}
            environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
            subprocess.run([str(self.node), "--input-type=module", "-e", script, str(self.supervisor)],
                cwd=self.code, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, check=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
            self.profile = {"user_id": self.scope.user_id, "account_id": self.scope.account_id, "provider": self.provider,
                "workspace": str(self.workspace), "home": str(self.home), "supervisor_directory": str(self.supervisor)}
            if self.ui_tools:
                self.profile['ui_tools'] = True
            # All capsule ancestors/files are already checked for reparse
            # points by the server. Avoid Node's extra realpath traversal into
            # inaccessible parent directories; do not broaden their ACLs.
            self.command = (str(self.worker_node), "--preserve-symlinks", "--preserve-symlinks-main", str(self.code / "host.mjs"))
            self._leased_home()
            return self
        except Exception:
            self.close()
            raise SandboxUnavailable("Worker preparation unavailable") from None

    def close(self):
        if self.temporary is not None:
            # Our freshly minted direct child only, never a caller-supplied
            # deletion target. Python3.13 removes junctions without traversing.
            if physical_path(self.base).parent != self.root or not self.base.name.startswith("worker-"):
                raise SandboxUnavailable("Worker cleanup unavailable")
            for attempt in range(10):
                try:
                    self.temporary.cleanup()
                    self.temporary = None
                    break
                except PermissionError:
                    if attempt == 9:
                        raise SandboxUnavailable("Worker cleanup unverified") from None
                    time.sleep(0.05)

    def __exit__(self, *args):
        self.close()
