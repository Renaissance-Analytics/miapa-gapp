"""Scoped backend checks, not the full product/release pipeline.

Alignment runs first, before importing Django or app/test modules.
"""
import os
from pathlib import Path
import sys

from tools.alignment import check_alignment, discovered_test_ids


def required_result_code(result):
    if result.testsRun == 0 or result.skipped or result.expectedFailures:
        return 1
    return int(not result.wasSuccessful())


def suite_test_ids(suite):
    for item in suite:
        if hasattr(item, "id"):
            yield item.id()
        else:
            yield from suite_test_ids(item)


def main():
    root = Path(__file__).resolve().parents[1]
    errors = check_alignment(root)
    if errors:
        print("Alignment FAIL\n" + "\n".join(errors))
        return 1
    print("Alignment PASS (first stage)", flush=True)
    if os.environ.get("DJANGO_SETTINGS_MODULE") not in (None, "backend.maipa.test_settings"):
        print("Refusing to run checks with non-test settings.")
        return 1
    os.environ["DJANGO_SETTINGS_MODULE"] = "backend.maipa.test_settings"

    import django
    from django.test.runner import DiscoverRunner

    class RequiredTestsRunner(DiscoverRunner):
        def build_suite(self, *args, **kwargs):
            suite = super().build_suite(*args, **kwargs)
            if set(suite_test_ids(suite)) != discovered_test_ids(root):
                raise ValueError("Runtime test discovery differs from reviewed static coverage.")
            return suite

        def suite_result(self, suite, result, **kwargs):
            failures = super().suite_result(suite, result, **kwargs)
            if required_result_code(result) and not failures:
                print("Required tests missing/skipped/expected-failure: FAIL")
            return max(failures, required_result_code(result))

    django.setup()
    return int(bool(RequiredTestsRunner(verbosity=2).run_tests(["tests"])))


if __name__ == "__main__":
    raise SystemExit(main())
