"""Lock parsing must accept real npm bundles without accepting untrusted closure."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.check_dependencies import verify_lock


class DependencyCheckTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="maipa-lock-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = {"dependencies": {"published": "1.2.3"}}
        self.lock = {"lockfileVersion": 3, "packages": {
            "": self.manifest,
            "node_modules/published": {"version": "1.2.3", "resolved": "https://registry.npmjs.org/published/-/published-1.2.3.tgz", "integrity": "sha512-synthetic"},
        }}
        self.install("published", "1.2.3")

    def install(self, name, version):
        path = self.root / "node_modules" / name / "package.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"name": name, "version": version}), encoding="utf-8")

    def verify(self):
        (self.root / "package.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        (self.root / "package-lock.json").write_text(json.dumps(self.lock), encoding="utf-8")
        return verify_lock(self.root)

    def test_accepts_exact_registry_install_and_integrity_covered_optional_bundle(self):
        self.lock["packages"]["node_modules/published/node_modules/bundled"] = {
            "version": "2.0.0", "optional": True, "inBundle": True}
        self.assertEqual(self.verify(), 1)

    def test_bundle_marker_without_verified_parent_is_refused(self):
        self.lock["packages"]["node_modules/untrusted"] = {
            "version": "2.0.0", "optional": True, "inBundle": True}
        with self.assertRaises(ValueError):
            self.verify()

    def test_extra_installed_package_or_wrong_version_is_refused(self):
        self.install("unexpected", "9.0.0")
        with self.assertRaises(ValueError):
            self.verify()
        self.install("published", "1.2.4")
        with self.assertRaises(ValueError):
            self.verify()

    def test_missing_required_unpinned_or_foreign_registry_dependency_is_refused(self):
        self.lock["packages"]["node_modules/missing"] = {
            "version": "1.0.0", "resolved": "https://registry.npmjs.org/missing/-/missing-1.0.0.tgz", "integrity": "sha512-synthetic"}
        with self.assertRaises(ValueError):
            self.verify()
        del self.lock["packages"]["node_modules/missing"]
        package = self.lock["packages"]["node_modules/published"]
        for change in ({"resolved": "https://untrusted.invalid/archive.tgz"}, {"integrity": ""}, {"link": True}):
            original = dict(package)
            package.update(change)
            with self.assertRaises(ValueError):
                self.verify()
            package.clear()
            package.update(original)
        self.manifest["dependencies"]["published"] = "^1.2.3"
        with self.assertRaises(ValueError):
            self.verify()
