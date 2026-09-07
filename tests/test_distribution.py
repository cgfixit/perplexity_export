"""Run the shipped ZIP in a separate process with site-packages disabled."""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from scripts.build_release import build
from scripts.verify_release import verify
from test_export import UID, snapshot, turn

ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.archive = build(ROOT, self.root / "dist")
        self.sums = self.archive.parent / "SHA256SUMS.txt"

    def resign(self):
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.sums.write_text(f"{digest}  {self.archive.name}\n", encoding="utf-8")

    def test_rejects_extra_member_even_with_matching_checksum(self):
        with zipfile.ZipFile(self.archive, "a") as package:
            package.writestr("perplexity_export/pplx_cookies.txt", "fixture=fake")
        self.resign()
        with self.assertRaisesRegex(ValueError, "allowlist"):
            verify(self.archive, self.sums)

    def test_rejects_duplicate_member_even_with_matching_checksum(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archive, "a") as package:
                package.writestr("perplexity_export/README.md", "replacement")
        self.resign()
        with self.assertRaisesRegex(ValueError, "allowlist"):
            verify(self.archive, self.sums)

    def test_rejects_symlink_and_directory_in_allowlisted_slot(self):
        original = self.archive.read_bytes()
        for mode in (0o120777, 0o040755):
            with self.subTest(mode=mode):
                self.archive.write_bytes(original)
                with zipfile.ZipFile(self.archive) as package:
                    members = [(info, package.read(info)) for info in package.infolist()]
                with zipfile.ZipFile(self.archive, "w") as package:
                    for info, data in members:
                        if info.filename == "perplexity_export/README.md":
                            info.external_attr = mode << 16
                        package.writestr(info, data)
                self.resign()
                with self.assertRaisesRegex(ValueError, "regular file"):
                    verify(self.archive, self.sums)

    def test_rejects_changed_archive_bytes(self):
        with self.archive.open("ab") as stream:
            stream.write(b"unexpected trailing bytes")
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify(self.archive, self.sums)

    def test_rejects_ambiguous_or_misdirected_checksum(self):
        valid = self.sums.read_text(encoding="utf-8")
        for content in ("", valid + valid, valid.replace(self.archive.name, "other.zip")):
            with self.subTest(content=content):
                self.sums.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    verify(self.archive, self.sums)

    def run_cli(self, script, *args):
        # Absolute script, unrelated cwd, no site-packages: detect checkout-only imports.
        result = subprocess.run(
            [sys.executable, "-S", str(script), *map(str, args)],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30, check=False,
        )
        return result

    def test_shipped_cli_rebuild_round_trip_and_failed_rerun(self):
        self.assertTrue(verify(self.archive, self.sums))
        with zipfile.ZipFile(self.archive) as package:
            package.extractall(self.root / "unpacked")
        shipped = self.root / "unpacked" / "perplexity_export"
        verified = self.run_cli(shipped / "scripts" / "verify_release.py", self.archive, self.sums)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        raw = self.root / "input"; raw.mkdir()
        fixture = snapshot([turn(1, "Unicode café 日本語 🚀"), turn(2, "```python\nprint('ok')\n```")])
        source = raw / f"{UID}.json"
        source.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
        out = self.root / "output with spaces"
        script = shipped / "pplx_export.py"
        result = self.run_cli(script, "--from-raw", raw, "-o", out)
        self.assertEqual(result.returncode, 0, result.stderr)
        markdown = out / "markdown" / f"chat_{UID}.md"
        saved = markdown.read_bytes()
        self.assertIn("日本語 🚀", saved.decode("utf-8"))
        self.assertIn("print('ok')", saved.decode("utf-8"))
        report = json.loads((out / "export_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["exported"], 1)
        self.assertEqual(report["status"], "completed_requested_scope")
        self.assertEqual(report["account_completeness"], "not_verified")
        record = json.loads((out / "records" / f"{UID}.json").read_text(encoding="utf-8"))
        self.assertEqual(record["turns"], 2)
        rebuilt = self.root / "rebuilt"
        result = self.run_cli(script, "--from-raw", out / "raw", "-o", rebuilt)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(saved, (rebuilt / "markdown" / markdown.name).read_bytes())
        source.write_bytes(b"\xff")
        result = self.run_cli(script, "--from-raw", raw, "-o", out)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(saved, markdown.read_bytes())
        self.assertFalse((out / ".export.lock").exists())
        report = json.loads((out / "export_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "needs_review")
