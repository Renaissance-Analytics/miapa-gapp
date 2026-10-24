"""Fail-closed acceptance traceability, run before importing/executing app tests.

Hashes preserve a recorded semantic review; they cannot perform that review.
No test, application module, or manifest-supplied code is executed here.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import unittest


ASSERTIONS = {name for name in dir(unittest.TestCase) if name.startswith("assert")}
SKIPS = {"skip", "skipIf", "skipUnless", "skipTest", "SkipTest", "expectedFailure"}


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _name(node.func)
    return ""


def _literal(node):
    try:
        ast.literal_eval(node)
        return True
    except (ValueError, TypeError):
        return False


def _asserts(method, methods, visited=None):
    visited = set() if visited is None else visited
    if method.name in visited:
        return False
    visited.add(method.name)
    for node in ast.walk(method):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if _name(node.func.value) != "self":
            continue
        name = node.func.attr
        if name in ASSERTIONS and node.args and not all(_literal(arg) for arg in node.args):
            return True
        if name in methods and _asserts(methods[name], methods, visited):
            return True
    return False


def _discover(root, errors):
    tests = {}
    test_root = root / "tests"
    for path in sorted(test_root.rglob("test_*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError, ValueError, UnicodeError) as exc:
            errors.append(f"Invalid test source {path.name}: {type(exc).__name__}")
            continue
        # Only declared unittest/Django bases are supported. Fake classes with
        # test-shaped methods do not count as executable tests.
        bases = {"unittest.TestCase"}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module in {"unittest", "django.test"}:
                for alias in node.names:
                    if alias.name in {"TestCase", "SimpleTestCase", "TransactionTestCase"}:
                        bases.add(alias.asname or alias.name)
        for cls in [node for node in tree.body if isinstance(node, ast.ClassDef)]:
            if not any(_name(base) in bases for base in cls.bases):
                continue
            methods = {node.name: node for node in cls.body if isinstance(node, ast.FunctionDef)}
            module = ".".join(path.relative_to(root).with_suffix("").parts)
            for method in methods.values():
                if not method.name.startswith("test_"):
                    continue
                test_id = f"{module}.{cls.name}.{method.name}"
                if test_id in tests:
                    errors.append(f"Duplicate test: {test_id}")
                # Universal newline normalization keeps review hashes stable on
                # Windows checkouts and Linux CI without masking code changes.
                tests[test_id] = hashlib.sha256(source.encode("utf-8")).hexdigest()
                # Reject conditional skips too. Required coverage cannot be
                # satisfied by a test that may silently opt out at runtime.
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Name, ast.Attribute)):
                        name = _name(node).rsplit(".", 1)[-1]
                        if name in SKIPS:
                            errors.append(f"Forbidden skip/expectedFailure ({name}): {test_id}")
                if not _asserts(method, methods):
                    errors.append(f"No meaningful assertion: {test_id}")
    if not tests:
        errors.append("No tests discovered")
    return tests


def _load(path, errors):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise ValueError("schema")
        return value
    except (OSError, ValueError, UnicodeError):
        errors.append(f"Invalid or missing {path.name}")
        return {}


def discovered_test_ids(root):
    errors = []
    tests = _discover(Path(root), errors)
    if errors:
        raise ValueError("\n".join(errors))
    return set(tests)


def check_alignment(root):
    root = Path(root)
    errors = []
    manifest = _load(root / "tests/acceptance.json", errors)
    review_doc = _load(root / "tests/review.json", errors)
    discovered = _discover(root, errors)
    requirements = manifest.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        return errors + ["Nonempty requirements required"]
    reviews = review_doc.get("reviews", [])
    if not isinstance(reviews, list):
        reviews = []
        errors.append("Invalid reviews list")
    review_index = {}
    for review in reviews:
        if not isinstance(review, dict):
            errors.append("Invalid review entry")
            continue
        pair = (review.get("scenario"), review.get("test"))
        if not all(isinstance(value, str) for value in pair):
            errors.append("Invalid review identity")
            continue
        if pair in review_index:
            errors.append(f"Duplicate review: {pair}")
        review_index[pair] = review
    ids, scenario_ids, mapped, used_reviews = set(), set(), set(), set()
    for requirement in requirements:
        if not isinstance(requirement, dict):
            errors.append("Invalid requirement")
            continue
        req_id = requirement.get("id")
        if not isinstance(req_id, str) or not req_id.strip():
            errors.append("Invalid requirement id")
            continue
        if req_id in ids:
            errors.append(f"Duplicate requirement: {req_id}")
        ids.add(req_id)
        if not isinstance(requirement.get("risk"), str) or not requirement["risk"].strip():
            errors.append(f"Missing risk: {req_id}")
        scenarios = requirement.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios:
            errors.append(f"Missing scenarios: {req_id}")
            continue
        kinds = {s.get("kind") for s in scenarios if isinstance(s, dict) and isinstance(s.get("kind"), str)}
        if not {"positive", "negative"}.issubset(kinds):
            errors.append(f"Both positive and negative scenarios required: {req_id}")
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                errors.append(f"Invalid scenario: {req_id}")
                continue
            sid = scenario.get("id")
            if not isinstance(sid, str) or not sid.strip():
                errors.append(f"Invalid scenario id: {req_id}")
                continue
            if sid in scenario_ids:
                errors.append(f"Duplicate scenario: {sid}")
            scenario_ids.add(sid)
            for field in ("given", "when", "then"):
                if not isinstance(scenario.get(field), str) or not scenario[field].strip():
                    errors.append(f"Missing {field}: {sid}")
            refs = scenario.get("tests")
            if not isinstance(refs, list) or not refs:
                errors.append(f"Missing test mapping: {sid}")
                continue
            digest = hashlib.sha256(json.dumps(scenario, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            for test in refs:
                if not isinstance(test, str):
                    errors.append(f"Invalid test mapping: {sid}")
                    continue
                mapped.add(test)
                if test not in discovered:
                    errors.append(f"Test not discovered: {test}")
                pair = (sid, test)
                review = review_index.get(pair)
                if review is None:
                    errors.append(f"Missing semantic review: {sid} -> {test}")
                    continue
                used_reviews.add(pair)
                if review.get("scenario_sha256") != digest:
                    errors.append(f"Review scenario changed: {sid}")
                if review.get("source_sha256") != discovered.get(test):
                    errors.append(f"Review source changed: {test}")
                if (review.get("decision") != "aligned" or
                        review.get("expected_observation") != scenario.get("then") or
                        not isinstance(review.get("reason"), str) or not review["reason"].strip() or
                        not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip()):
                    errors.append(f"Incomplete semantic review: {sid} -> {test}")
    for test in sorted(discovered.keys() - mapped):
        errors.append(f"Unmapped test: {test}")
    for pair in review_index.keys() - used_reviews:
        errors.append(f"Stale review mapping: {pair}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    errors = check_alignment(args.root)
    if errors:
        print("Alignment FAIL\n" + "\n".join(errors))
        return 1
    print("Alignment PASS: discovered tests mapped to reviewed positive/negative acceptance scenarios.")
    print("Traceability preserves recorded review; semantic correctness still requires independent behavior review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
