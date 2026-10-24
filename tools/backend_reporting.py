"""Bounded public test diagnostics: no exception text, tracebacks or paths."""
import unittest

KINDS = frozenset({"AssertionError", "OSError", "PermissionError", "FileNotFoundError", "TimeoutError",
    "ValueError", "TypeError", "AttributeError", "RuntimeError", "ImportError", "ModuleNotFoundError",
    "JSONDecodeError", "CalledProcessError", "SandboxUnavailable", "TransportUnavailable", "AcpError"})
OPERATIONS = frozenset({"native", "create_profile", "grant_acl", "attributes", "pipes", "job_create",
    "environment", "create_process", "assign_job", "verify_token", "resume"})


def _frame(kind, operation=None, native_code=None):
    frame = {"kind": kind if kind in KINDS else "unavailable"}
    if operation in OPERATIONS:
        frame["operation"] = operation
        if type(native_code) is int and 0 <= native_code < 2 ** 32:
            frame["native_code"] = native_code
    return frame


def failure_record(identifier, error, known_tests):
    chain, seen = [], set()
    while isinstance(error, BaseException) and id(error) not in seen and len(chain) < 8:
        seen.add(id(error))
        chain.append(_frame(type(error).__name__, getattr(error, "operation", None), getattr(error, "native_code", None)))
        error = error.__cause__ or error.__context__
    return {"test": identifier if identifier in known_tests else "unavailable", "chain": chain}


class ReportedTextTestResult(unittest.TextTestResult):
    known_tests = frozenset()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.safe_failures = []

    def _record(self, test, err):
        if len(self.safe_failures) < 64:
            self.safe_failures.append(failure_record(test.id(), err[1], self.known_tests))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, err)

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, err)


def failure_evidence(value, known_tests):
    result = {"returncode": 1}
    if not isinstance(value, dict):
        return result
    for key in ("tests", "passed", "failed", "skipped"):
        count = value.get(key)
        if type(count) is int and 0 <= count <= 1_000_000:
            result[key] = count
    result["failures"] = []
    failures = value.get("failures")
    for row in failures[:64] if isinstance(failures, list) else []:
        if not isinstance(row, dict):
            continue
        frames = row.get("chain")
        chain = [_frame(frame.get("kind"), frame.get("operation"), frame.get("native_code"))
            for frame in frames[:8] if isinstance(frame, dict)] if isinstance(frames, list) else []
        identifier = row.get("test")
        result["failures"].append({"test": identifier if isinstance(identifier, str) and identifier in known_tests else "unavailable", "chain": chain})
    return result
