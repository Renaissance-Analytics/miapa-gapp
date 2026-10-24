"""App-owned native Windows boundary. No shell, provider SDK or fallback launch.

The trusted server owns this API, its immutable Scope, paths and command. Nothing
here is an HTTP/agent command surface. A process starts suspended, its token/job
are verified, then it runs. Only the three pipe handles cross that boundary.
"""
import asyncio
import ctypes as c
from ctypes import wintypes as w
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
import uuid

from backend.maipa.acp import Scope


class SandboxUnavailable(OSError):
    """Public-safe native failure, without paths, SIDs or OS diagnostics."""


class _Startup(c.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
        ("x", w.DWORD), ("y", w.DWORD), ("xsize", w.DWORD), ("ysize", w.DWORD),
        ("xchars", w.DWORD), ("ychars", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
        ("show", w.WORD), ("reservedsize", w.WORD), ("reserved2", c.POINTER(c.c_byte)),
        ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]


class _StartupEx(c.Structure):
    _fields_ = [("base", _Startup), ("attributes", c.c_void_p)]


class _ProcessInfo(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


class _SecurityCapabilities(c.Structure):
    _fields_ = [("sid", c.c_void_p), ("capabilities", c.c_void_p), ("count", w.DWORD), ("reserved", w.DWORD)]


class _BasicLimits(c.Structure):
    _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
        ("min_working", c.c_size_t), ("max_working", c.c_size_t), ("active_limit", w.DWORD),
        ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]


class _IoCounters(c.Structure):
    _fields_ = [(name, c.c_uint64) for name in ("reads", "writes", "others", "read_bytes", "write_bytes", "other_bytes")]


class _ExtendedLimits(c.Structure):
    _fields_ = [("basic", _BasicLimits), ("io", _IoCounters), ("process_memory", c.c_size_t),
        ("job_memory", c.c_size_t), ("peak_process_memory", c.c_size_t), ("peak_job_memory", c.c_size_t)]


class _Accounting(c.Structure):
    _fields_ = [("user", c.c_int64), ("kernel", c.c_int64), ("period_user", c.c_int64),
        ("period_kernel", c.c_int64), ("faults", w.DWORD), ("total", w.DWORD),
        ("active", w.DWORD), ("terminated", w.DWORD)]


class _Api:
    def __init__(self):
        if os.name != "nt":
            raise SandboxUnavailable("Windows worker isolation unavailable")
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.userenv = c.WinDLL("userenv", use_last_error=True)
        self.advapi = c.WinDLL("advapi32", use_last_error=True)
        def define(dll, name, args, result=w.BOOL):
            fn = getattr(dll, name)
            fn.argtypes, fn.restype = args, result
            return fn
        k, a, u = self.kernel, self.advapi, self.userenv
        define(u, "CreateAppContainerProfile", [w.LPCWSTR, w.LPCWSTR, w.LPCWSTR, c.c_void_p, w.DWORD, c.POINTER(c.c_void_p)], c.c_long)
        define(u, "DeleteAppContainerProfile", [w.LPCWSTR], c.c_long)
        define(u, "CreateEnvironmentBlock", [c.POINTER(c.c_void_p), w.HANDLE, w.BOOL])
        define(u, "DestroyEnvironmentBlock", [c.c_void_p])
        define(k, "GetCurrentProcess", [], w.HANDLE)
        define(a, "ConvertSidToStringSidW", [c.c_void_p, c.POINTER(w.LPWSTR)])
        define(a, "FreeSid", [c.c_void_p], c.c_void_p)
        define(a, "OpenProcessToken", [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)])
        define(a, "GetTokenInformation", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.POINTER(w.DWORD)])
        define(a, "EqualSid", [c.c_void_p, c.c_void_p])
        define(k, "LocalFree", [c.c_void_p], c.c_void_p)
        define(k, "InitializeProcThreadAttributeList", [c.c_void_p, w.DWORD, w.DWORD, c.POINTER(c.c_size_t)])
        define(k, "UpdateProcThreadAttribute", [c.c_void_p, w.DWORD, c.c_size_t, c.c_void_p, c.c_size_t, c.c_void_p, c.c_void_p])
        define(k, "DeleteProcThreadAttributeList", [c.c_void_p], None)
        define(k, "CreateProcessW", [w.LPCWSTR, w.LPWSTR, c.c_void_p, c.c_void_p, w.BOOL, w.DWORD, c.c_void_p, w.LPCWSTR, c.c_void_p, c.POINTER(_ProcessInfo)])
        define(k, "CreateJobObjectW", [c.c_void_p, w.LPCWSTR], w.HANDLE)
        define(k, "SetInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD])
        define(k, "QueryInformationJobObject", [w.HANDLE, c.c_int, c.c_void_p, w.DWORD, c.c_void_p])
        define(k, "AssignProcessToJobObject", [w.HANDLE, w.HANDLE])
        define(k, "IsProcessInJob", [w.HANDLE, w.HANDLE, c.POINTER(w.BOOL)])
        define(k, "TerminateJobObject", [w.HANDLE, w.UINT])
        define(k, "ResumeThread", [w.HANDLE], w.DWORD)
        define(k, "WaitForSingleObject", [w.HANDLE, w.DWORD], w.DWORD)
        define(k, "GetExitCodeProcess", [w.HANDLE, c.POINTER(w.DWORD)])
        define(k, "TerminateProcess", [w.HANDLE, w.UINT])
        define(k, "CloseHandle", [w.HANDLE])
        define(k, "OpenProcess", [w.DWORD, w.BOOL, w.DWORD], w.HANDLE)


def _checked(value):
    if not value:
        error = SandboxUnavailable("Windows worker isolation unavailable")
        error.native_code = c.get_last_error()
        raise error
    return value


def _windows_paths(api):
    """Resolve only OS startup paths even when the caller/CI env is scrubbed."""
    token, block = w.HANDLE(), c.c_void_p()
    _checked(api.advapi.OpenProcessToken(api.kernel.GetCurrentProcess(), 0xA, c.byref(token)))
    try:
        _checked(api.userenv.CreateEnvironmentBlock(c.byref(block), token, False))
        result, offset = {}, 0
        allowed = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}
        while offset < 32767:
            entry = c.wstring_at(block.value + offset * c.sizeof(c.c_wchar))
            if not entry:
                return result
            offset += len(entry) + 1
            key, separator, value = entry.partition("=")
            if separator and key.upper() in allowed:
                result[key.upper()] = value
        raise SandboxUnavailable("Worker environment unavailable")
    finally:
        if block:
            api.userenv.DestroyEnvironmentBlock(block)
        api.kernel.CloseHandle(token)


def physical_path(value, *, directory=True):
    """Reject every reparse component before resolving, including junctions."""
    path = Path(value)
    if not path.is_absolute() or str(path).startswith("\\\\"):
        raise SandboxUnavailable("Worker path unavailable")
    for part in (path, *path.parents):
        metadata = part.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise SandboxUnavailable("Worker path unavailable")
    if (directory and not path.is_dir()) or (not directory and not path.is_file()):
        raise SandboxUnavailable("Worker path unavailable")
    return path.resolve(strict=True)


class _PipeReader:
    def __init__(self, fd, limit):
        self.fd, self.limit, self.buffer, self.lock = fd, limit, bytearray(), threading.Lock()

    def _read(self, size):
        with self.lock:
            return os.read(self.fd, size) if self.fd is not None else b""

    async def read(self, size):
        return await asyncio.to_thread(self._read, min(size, 4096))

    async def readline(self):
        while True:
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                result = bytes(self.buffer[:newline + 1])
                del self.buffer[:newline + 1]
                return result
            if len(self.buffer) >= self.limit:
                raise ValueError("Worker frame budget")
            chunk = await self.read(min(4096, self.limit - len(self.buffer)))
            if not chunk:
                result, self.buffer = bytes(self.buffer), bytearray()
                return result
            self.buffer.extend(chunk)

    def close(self):
        with self.lock:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None


class _PipeWriter:
    def __init__(self, fd, limit):
        self.fd, self.limit, self.pending, self.closing = fd, limit, b"", False
        self.lock = threading.Lock()

    def write(self, data):
        if self.closing or self.pending or len(data) > self.limit:
            raise SandboxUnavailable("Worker pipe unavailable")
        self.pending = data

    def _drain(self):
        with self.lock:
            try:
                data, self.pending = self.pending, b""
                while data:
                    data = data[os.write(self.fd, data):]
            finally:
                if self.closing and self.fd is not None:
                    os.close(self.fd)
                    self.fd = None

    async def drain(self):
        await asyncio.to_thread(self._drain)

    def close(self):
        # Never block the event loop on a full pipe: killing the job releases a
        # pending native write, whose finally block then closes this descriptor.
        self.closing = True
        if self.lock.acquire(blocking=False):
            try:
                if self.fd is not None:
                    os.close(self.fd)
                    self.fd = None
            finally:
                self.lock.release()


class _NativeProcess:
    def __init__(self, api, process, job, stdin, stdout, stderr):
        self.api, self.handle, self.job, self.pid = api, process.process, job, process.pid
        self.stdin, self.stdout, self.stderr = stdin, stdout, stderr
        self.returncode, self.job_empty, self.isolation = None, False, "windows-appcontainer"
        self._lock, self._waiter = threading.Lock(), None

    def _active(self):
        value = _Accounting()
        _checked(self.api.kernel.QueryInformationJobObject(self.job, 1, c.byref(value), c.sizeof(value), None))
        return value.active

    def kill(self):
        if self.job:
            _checked(self.api.kernel.TerminateJobObject(self.job, 1))

    def _finish(self):
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            k = self.api.kernel
            if k.WaitForSingleObject(self.handle, 0xffffffff) != 0:
                raise SandboxUnavailable("Worker stop unverified")
            code = w.DWORD()
            _checked(k.GetExitCodeProcess(self.handle, c.byref(code)))
            # A root exit does not establish descendant exit. Kill any remainder
            # and observe the native job's actual active-process count.
            self.kill()
            deadline = time.monotonic() + 5
            while self._active():
                if time.monotonic() >= deadline:
                    raise SandboxUnavailable("Worker stop unverified")
                time.sleep(0.01)
            self.job_empty = True
            self.stdin.close()
            self.stdout.close()
            self.stderr.close()
            _checked(k.CloseHandle(self.handle))
            _checked(k.CloseHandle(self.job))
            self.handle = self.job = None
            self.returncode = code.value
            return self.returncode

    async def wait(self):
        if self._waiter is None:
            self._waiter = asyncio.create_task(asyncio.to_thread(self._finish))
        return await asyncio.shield(self._waiter)


class WindowsSandbox:
    def __init__(self, scope, root, *, writable, readonly, installation_id):
        if not isinstance(scope, Scope) or not isinstance(installation_id, str) or not 1 <= len(installation_id) <= 200:
            raise SandboxUnavailable("Worker identity unavailable")
        self.scope, self.root = scope, physical_path(root)
        self.writable = tuple(physical_path(path) for path in writable)
        self.readonly = tuple(physical_path(path) for path in readonly)
        paths = (*self.writable, *self.readonly)
        if (not self.writable or any(path == self.root or not path.is_relative_to(self.root) for path in paths)
                or len(set(paths)) != len(paths)
                or any(a.is_relative_to(b) or b.is_relative_to(a) for i, a in enumerate(paths) for b in paths[i + 1:])):
            raise SandboxUnavailable("Worker layout unavailable")
        identity = hashlib.sha256((installation_id + "\0" + scope.user_id + "\0" + scope.account_id).encode()).hexdigest()[:16]
        self.profile_name = "Maipa.Worker." + identity + "." + uuid.uuid4().hex
        self.api, self.sid, self.process = _Api(), c.c_void_p(), None
        self.profile_removed, self.revoked, self._entered, self._created = False, False, False, False

    def _acl(self, path, permission, *, inherit=False, low=False):
        tool = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/icacls.exe"
        entry = "*" + self._sid_string + ":" + ("(OI)(CI)" if inherit else "") + permission
        subprocess.run([str(tool), str(path), "/grant", entry], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        if low:
            subprocess.run([str(tool), str(path), "/setintegritylevel", "(OI)(CI)L"], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)

    def __enter__(self):
        operation = "create_profile"
        try:
            if self._entered:
                raise SandboxUnavailable("Worker already initialized")
            self._entered = True
            result = self.api.userenv.CreateAppContainerProfile(self.profile_name, "Maipa worker", "Private Maipa worker", None, 0, c.byref(self.sid))
            if result < 0:
                error = SandboxUnavailable("Worker profile unavailable")
                error.native_code = result & 0xffffffff
                raise error
            self._created = True
            value = w.LPWSTR()
            _checked(self.api.advapi.ConvertSidToStringSidW(self.sid, c.byref(value)))
            try:
                self._sid_string = value.value
            finally:
                self.api.kernel.LocalFree(c.cast(value, c.c_void_p))
            operation = "grant_acl"
            self._acl(self.root, "(X,RA)")
            for path in self.readonly:
                self._acl(path, "RX", inherit=True)
            for path in self.writable:
                self._acl(path, "M", inherit=True, low=True)
            return self
        except Exception as cause:
            error = SandboxUnavailable("Worker isolation unavailable")
            error.operation = operation
            error.native_code = getattr(cause, "native_code", getattr(cause, "returncode", c.get_last_error()))
            self.close()
            raise error from None

    def _verify_token(self, process, job):
        a, k, token = self.api.advapi, self.api.kernel, w.HANDLE()
        _checked(a.OpenProcessToken(process, 0x8, c.byref(token)))
        try:
            flag, size = w.DWORD(), w.DWORD()
            _checked(a.GetTokenInformation(token, 29, c.byref(flag), c.sizeof(flag), c.byref(size)))
            if flag.value != 1:
                raise SandboxUnavailable("Worker token unavailable")
            a.GetTokenInformation(token, 31, None, 0, c.byref(size))
            buffer = c.create_string_buffer(size.value)
            _checked(a.GetTokenInformation(token, 31, buffer, size.value, c.byref(size)))
            native_sid = c.cast(buffer, c.POINTER(c.c_void_p)).contents.value
            _checked(a.EqualSid(native_sid, self.sid))
            inside = w.BOOL()
            _checked(k.IsProcessInJob(process, job, c.byref(inside)))
            _checked(inside.value)
        finally:
            k.CloseHandle(token)

    def _spawn(self, command, cwd, env, limit):
        import msvcrt
        k, pi, attributes, job = self.api.kernel, _ProcessInfo(), None, None
        descriptors = []
        operation = "attributes"
        try:
            size = c.c_size_t()
            k.InitializeProcThreadAttributeList(None, 2, 0, c.byref(size))
            buffer = c.create_string_buffer(size.value)
            attributes = c.cast(buffer, c.c_void_p)
            _checked(k.InitializeProcThreadAttributeList(attributes, 2, 0, c.byref(size)))
            security = _SecurityCapabilities(self.sid, None, 0, 0)
            _checked(k.UpdateProcThreadAttribute(attributes, 0, 0x20009, c.byref(security), c.sizeof(security), None, None))
            operation = "pipes"
            input_read, input_write = os.pipe()
            output_read, output_write = os.pipe()
            error_read, error_write = os.pipe()
            descriptors.extend((input_read, input_write, output_read, output_write, error_read, error_write))
            handles = (w.HANDLE * 3)(*[msvcrt.get_osfhandle(fd) for fd in (input_read, output_write, error_write)])
            for handle in handles:
                os.set_handle_inheritable(handle, True)
            _checked(k.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles, c.sizeof(handles), None, None))
            operation = "job_create"
            job = _checked(k.CreateJobObjectW(None, None))
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway flags.
            _checked(k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)))
            startup = _StartupEx()
            startup.base.cb, startup.base.flags = c.sizeof(startup), 0x100
            startup.base.stdin, startup.base.stdout, startup.base.stderr = handles
            startup.attributes = attributes
            operation = "environment"
            child_env = _windows_paths(self.api)
            child_env.update({key: value for key, value in env.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "MAIPA_ACP_PROFILE"}})
            child_env.update(TEMP=str(cwd), TMP=str(cwd))
            block = c.create_unicode_buffer("\0".join(key + "=" + value for key, value in sorted(child_env.items(), key=lambda pair: pair[0].upper())) + "\0\0")
            operation = "create_process"
            _checked(k.CreateProcessW(command[0], c.create_unicode_buffer(subprocess.list2cmdline(command)), None, None, True,
                0x80000 | 0x400 | 0x08000000 | 0x4, block, str(cwd), c.byref(startup), c.byref(pi)))
            operation = "assign_job"
            _checked(k.AssignProcessToJobObject(job, pi.process))
            operation = "verify_token"
            self._verify_token(pi.process, job)
            operation = "resume"
            if k.ResumeThread(pi.thread) == 0xffffffff:
                raise SandboxUnavailable("Worker launch unavailable")
            for fd in (input_read, output_write, error_write):
                os.close(fd)
                descriptors.remove(fd)
            child = _NativeProcess(self.api, pi, job, _PipeWriter(input_write, limit), _PipeReader(output_read, limit), _PipeReader(error_read, limit))
            descriptors.clear()
            job = pi.process = None
            return child
        except Exception as cause:
            error = SandboxUnavailable("Worker launch unavailable")
            error.operation = operation
            error.native_code = getattr(cause, "native_code", c.get_last_error())
            if pi.process:
                k.TerminateProcess(pi.process, 1)
                k.WaitForSingleObject(pi.process, 5000)
            raise error from None
        finally:
            if pi.thread: k.CloseHandle(pi.thread)
            if pi.process: k.CloseHandle(pi.process)
            if job: k.CloseHandle(job)
            if attributes: k.DeleteProcThreadAttributeList(attributes)
            for fd in descriptors:
                os.close(fd)

    async def spawn(self, command, *, scope, cwd, env, limit):
        if scope != self.scope:
            raise PermissionError("Worker scope unavailable")
        cwd = physical_path(cwd)
        if (not self._created or self.revoked or self.process is not None or cwd not in self.writable or
                not isinstance(command, (tuple, list)) or not command or not all(isinstance(arg, str) and arg and "\0" not in arg for arg in command)
                or not Path(command[0]).is_absolute() or not Path(command[0]).is_file() or type(limit) is not int or not 1024 <= limit <= 1048577):
            raise SandboxUnavailable("Worker launch unavailable")
        task = asyncio.create_task(asyncio.to_thread(self._spawn, command, cwd, env, limit))
        try:
            self.process = await asyncio.shield(task)
        except asyncio.CancelledError:
            self.process = await asyncio.shield(task)
            self.process.kill()
            await self.process.wait()
            raise
        return self.process

    def revoke(self, scope):
        if scope != self.scope:
            raise PermissionError("Worker scope unavailable")
        self.revoked = True
        if self.process is not None:
            self.process.kill()

    def close(self):
        if self.process is not None and self.process.returncode is None:
            self.process.kill()
            self.process._finish()
        if self.sid:
            self.api.advapi.FreeSid(self.sid)
            self.sid = c.c_void_p()
        if self._created:
            self.profile_removed = self.api.userenv.DeleteAppContainerProfile(self.profile_name) >= 0
            self._created = False
            if not self.profile_removed:
                raise SandboxUnavailable("Worker cleanup unverified")

    def __exit__(self, *args):
        self.close()


class ProcessWitness:
    """Read-only handle evidence; a recycled PID cannot replace this process."""
    def __init__(self, pid):
        self.api = _Api()
        self.handle = _checked(self.api.kernel.OpenProcess(0x100000 | 0x1000, False, pid))

    @property
    def exited(self):
        return self.wait(0)

    def wait(self, seconds):
        return self.api.kernel.WaitForSingleObject(self.handle, int(seconds * 1000)) == 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.api.kernel.CloseHandle(self.handle)


def remove_abandoned_profile(name):
    if not isinstance(name, str) or not re.fullmatch(r"Maipa\.Worker\.[0-9a-f]{16}\.[0-9a-f]{32}", name):
        raise SandboxUnavailable("Worker profile unavailable")
    return _Api().userenv.DeleteAppContainerProfile(name) >= 0
