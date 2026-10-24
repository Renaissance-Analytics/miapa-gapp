"""Application-owned Python client for published Prism ACP stdio.

Commands and immutable scopes come from trusted backend code, never request
JSON. This module supplies no HTTP endpoint, login, OS jail or business approval.
"""
import asyncio
from collections import deque
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import uuid


class AcpError(Exception):
    """Safe error; raw provider messages are never reflected."""


@dataclass(frozen=True)
class Scope:
    user_id: str
    account_id: str

    def __post_init__(self):
        for value in (self.user_id, self.account_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError("Invalid ACP owner")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError("Invalid number")


class AcpClient:
    def __init__(self, command, scope, workspace, *, env=None, timeout=30, budget=1048576, launcher=None):
        if (not isinstance(scope, Scope) or not isinstance(command, (list, tuple)) or
                not command or not all(isinstance(arg, str) and arg and "\0" not in arg for arg in command) or
                not Path(command[0]).is_absolute() or not Path(command[0]).is_file()):
            raise ValueError("Invalid ACP launch")
        workspace = Path(workspace)
        if not workspace.is_absolute() or not workspace.is_dir():
            raise ValueError("Invalid ACP workspace")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
                not math.isfinite(timeout) or not 0 < timeout <= 600 or
                type(budget) is not int or not 1024 <= budget <= 1048576):
            raise ValueError("Invalid ACP budget")
        self.workspace = workspace.resolve()
        self._scope = scope
        self._launcher = launcher  # Trusted server capability, never HTTP configuration.
        self._command = tuple(command)
        self._env = {key: value for key, value in os.environ.items()
                     if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        # Only this application-specific launch record may be explicitly added.
        # API keys, NODE_OPTIONS and Python injection variables never propagate.
        if env:
            if set(env) - {"MAIPA_ACP_PROFILE"} or not all(isinstance(value, str) for value in env.values()):
                raise ValueError("Invalid ACP environment")
            if "MAIPA_ACP_PROFILE" in env:
                try:
                    raw = env["MAIPA_ACP_PROFILE"]
                    if len(raw.encode("utf-8")) > 16384:
                        raise ValueError("Profile budget")
                    profile = json.loads(raw, object_pairs_hook=_object, parse_constant=_invalid_number)
                    if (not isinstance(profile, dict) or profile.get("user_id") != scope.user_id or
                            profile.get("account_id") != scope.account_id or
                            not isinstance(profile.get("workspace"), str) or
                            not Path(profile["workspace"]).is_absolute() or
                            Path(profile["workspace"]).resolve() != self.workspace):
                        raise ValueError("Profile owner")
                except (ValueError, OSError, RecursionError):
                    raise PermissionError("ACP worker profile unavailable") from None
            self._env.update(env)
        self._timeout, self._budget = timeout, budget
        self._process = None
        self._reader = self._stderr = None
        self._failed = False
        self._pending = {}
        self._next_id = 0
        self._sessions = {}
        self._turns = set()
        self._updates = deque()
        self._update_bytes = 0
        self._write_lock = asyncio.Lock()
        self._stop_lock = asyncio.Lock()
        self._initialize_lock = asyncio.Lock()
        self._new_session_lock = asyncio.Lock()
        self._initialized = None
        self.shutdown_report = None

    @property
    def returncode(self):
        return self._process.returncode if self._process else None

    def _authorize(self, scope, session=None):
        if scope != self._scope or (session is not None and session not in self._sessions):
            raise PermissionError("ACP session unavailable")

    async def start(self):
        if self._process is not None or self._failed:
            raise AcpError("ACP connection unavailable")
        try:
            if self._launcher is None:
                self._process = await asyncio.create_subprocess_exec(*self._command,
                    cwd=self.workspace, env=self._env, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=self._budget + 1)
            else:
                self._process = await self._launcher.spawn(self._command, scope=self._scope,
                    cwd=self.workspace, env=self._env, limit=self._budget + 1)
        except OSError:
            self._failed = True
            raise AcpError("ACP connection unavailable") from None
        self._reader = asyncio.create_task(self._read())
        self._stderr = asyncio.create_task(self._discard_stderr())

    async def _discard_stderr(self):
        try:
            while await self._process.stderr.read(4096):
                pass
        except (OSError, ValueError):
            self._fail()
            await self._terminate()

    def _fail(self):
        self._failed = True
        self._updates.clear()
        self._update_bytes = 0
        for future in self._pending.values():
            if not future.done():
                future.set_exception(AcpError("ACP connection unavailable"))

    async def _write(self, message):
        if self._failed or self._process is None:
            raise AcpError("ACP connection unavailable")
        wire = json.dumps(message, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode() + b"\n"
        if len(wire) > self._budget:
            raise AcpError("ACP request exceeds application budget")
        async with self._write_lock:
            if self._failed:
                raise AcpError("ACP connection unavailable")
            self._process.stdin.write(wire)
            await self._process.stdin.drain()

    async def _read(self):
        try:
            while True:
                wire = await self._process.stdout.readline()
                if not wire or len(wire) > self._budget:
                    raise ValueError("Invalid frame size or EOF")
                message = json.loads(wire.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_number)
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise ValueError("Invalid envelope")
                await self._receive(message, len(wire))
        except (ValueError, UnicodeError, OSError, RecursionError, AcpError):
            self._fail()
            await self._terminate()

    async def _receive(self, message, wire_size):
        if "method" in message:
            method = message["method"]
            if not isinstance(method, str) or "result" in message or "error" in message:
                raise ValueError("Invalid method")
            if "id" in message:
                if type(message["id"]) not in (int, str):
                    raise ValueError("Invalid request identity")
                reply = {"jsonrpc": "2.0", "id": message["id"]}
                if method == "session/request_permission":
                    reply["result"] = {"outcome": {"outcome": "cancelled"}}
                else:
                    reply["error"] = {"code": -32601, "message": "Method not supported"}
                await self._write(reply)
            elif method == "session/update":
                params = message.get("params")
                if (not isinstance(params, dict) or not isinstance(params.get("sessionId"), str) or
                        not isinstance(params.get("update"), dict)):
                    raise ValueError("Invalid update")
                if len(self._updates) >= 1024 or self._update_bytes + wire_size > self._budget:
                    raise ValueError("Update budget exhausted")
                self._updates.append((params["sessionId"], params["update"], wire_size))
                self._update_bytes += wire_size
            return
        number = message.get("id")
        if type(number) is not int or number not in self._pending or ("result" in message) == ("error" in message):
            raise ValueError("Invalid response")
        future = self._pending[number]
        if future.done():
            raise ValueError("Duplicate response")
        if "error" in message:
            future.set_exception(AcpError("ACP request refused"))
        else:
            future.set_result(message["result"])

    async def _request(self, scope, method, params, *, timeout=None):
        """Internal protocol primitive; never expose arbitrary RPC to HTTP/UI."""
        self._authorize(scope)
        if self._failed or self._process is None or len(self._pending) >= 64:
            raise AcpError("ACP connection unavailable")
        self._next_id += 1
        number = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[number] = future
        try:
            # A worker that never reads can fill its input pipe. The deadline
            # covers writing as well as waiting for the correlated response.
            async with asyncio.timeout(self._timeout if timeout is None else timeout):
                await self._write({"jsonrpc": "2.0", "id": number, "method": method, "params": params})
                return await future
        except (TimeoutError, OSError):
            self._fail()
            await self._terminate()
            raise AcpError("ACP connection unavailable") from None
        except asyncio.CancelledError:
            self._fail()
            await self._terminate()
            raise
        finally:
            self._pending.pop(number, None)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Also consume a failure during a broken write.

    async def initialize(self, scope):
        self._authorize(scope)
        async with self._initialize_lock:
            if self._failed or self._process is None or self._process.returncode is not None:
                raise AcpError("ACP connection unavailable")
            if self._initialized is None:
                result = await self._request(scope, "initialize", {"protocolVersion": 1,
                    "clientInfo": {"name": "maipa", "version": "0.0.0"}, "clientCapabilities": {}})
                if not isinstance(result, dict) or type(result.get("protocolVersion")) is not int or result["protocolVersion"] != 1:
                    self._fail()
                    await self._terminate()
                    raise AcpError("ACP protocol unavailable")
                self._initialized = result
            return self._initialized.copy()

    async def new_session(self, scope):
        self._authorize(scope)
        async with self._new_session_lock:
            if len(self._sessions) >= 32:
                raise AcpError("ACP session budget exhausted")
            await self.initialize(scope)
            result = await self._request(scope, "session/new", {"cwd": str(self.workspace), "mcpServers": []})
            if (not isinstance(result, dict) or not isinstance(result.get("sessionId"), str) or
                    not result["sessionId"] or any(item["sessionId"] == result["sessionId"] for item in self._sessions.values())):
                self._fail()
                await self._terminate()
                raise AcpError("ACP session unavailable")
            handle = str(uuid.uuid4())
            self._sessions[handle] = result
            return handle

    def session_details(self, scope, session):
        self._authorize(scope, session)
        return self._sessions[session].copy()

    async def prompt(self, scope, session, text):
        self._authorize(scope, session)
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Invalid ACP prompt")
        if session in self._turns:
            raise AcpError("ACP turn already running")
        self._turns.add(session)
        try:
            return await self._request(scope, "session/prompt", {"sessionId": self._sessions[session]["sessionId"],
                "prompt": [{"type": "text", "text": text}]})
        finally:
            self._turns.discard(session)

    async def cancel(self, scope, session):
        self._authorize(scope, session)
        try:
            await self._write({"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": self._sessions[session]["sessionId"]}})
        except OSError:
            self._fail()
            await self._terminate()
            raise AcpError("ACP connection unavailable") from None

    def drain_updates(self, scope, session):
        self._authorize(scope, session)
        target = self._sessions[session]["sessionId"]
        selected, remaining = [], deque()
        for acp_id, update, size in self._updates:
            if acp_id == target:
                selected.append(update)
                self._update_bytes -= size
            else:
                remaining.append((acp_id, update, size))
        self._updates = remaining
        return selected

    async def shutdown(self, scope):
        self._authorize(scope)
        report = await self._request(scope, "maipa/shutdown", {}, timeout=min(self._timeout, 5))
        if (not isinstance(report, dict) or type(report.get("verified")) is not bool or
                any(type(report.get(field)) is not int or report[field] < 0 for field in ("survivors", "sessions")) or
                (report["verified"] and report["survivors"] != 0)):
            raise AcpError("ACP shutdown unverified")
        self.shutdown_report = {key: report[key] for key in ("verified", "survivors", "sessions")}
        return self.shutdown_report.copy()

    async def _terminate(self):
        async with self._stop_lock:
            if self._process is None or self._process.returncode is not None:
                return
            self._process.stdin.close()
            try:
                await asyncio.wait_for(self._process.wait(), 1)
            except TimeoutError:
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
                await self._process.wait()

    async def close(self):
        if self._process is not None and not self._failed and self.shutdown_report is None:
            try:
                await self.shutdown(self._scope)
            except AcpError:
                pass
        self._fail()
        await self._terminate()
        tasks = [task for task in (self._reader, self._stderr) if task is not None and task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._sessions.clear()
