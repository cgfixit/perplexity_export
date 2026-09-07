import contextlib
import re
import hashlib
import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import live_verify
import pplx_export as e
from scripts.build_release import FILES, build
from test_export import QueueClient, UID, detail


class URLTests(unittest.TestCase):
    def test_uuid_link_needs_no_history_discovery(self):
        client = QueueClient([])
        rows = e.select_threads(client, [], [f"https://www.perplexity.ai/search/{UID}?tracking=ignored#fragment"], lambda *a: None)
        self.assertEqual(rows, [{"uuid": UID}])
        self.assertEqual(client.calls, [])

    def test_computer_uuid_link(self):
        self.assertEqual(e.parse_thread_url(f"https://perplexity.ai/computer/tasks/{UID}"), UID)

    def test_shared_slug_resolves_through_authenticated_metadata(self):
        slug = "test-chat.a_B-123"
        client = QueueClient([[{"uuid": UID, "slug": slug}], [], []])
        with contextlib.redirect_stdout(io.StringIO()):
            rows = e.select_threads(client, [], ["https://www.perplexity.ai/search/" + slug], lambda *a: None)
        self.assertEqual(rows[0]["uuid"], UID)

    def test_unknown_shared_slug_is_not_used_as_uuid(self):
        client = QueueClient([[], []])
        with self.assertRaisesRegex(e.ExportError, "could not be resolved"):
            e.select_threads(client, [], ["https://www.perplexity.ai/search/not-in-history"], lambda *a: None)

    def test_rejects_nonperplexity_or_unsafe_urls(self):
        for url in ("http://www.perplexity.ai/search/x", "https://www.perplexity.ai.evil.test/search/x",
                    "https://user@www.perplexity.ai/search/x", "https://www.perplexity.ai:443/search/x",
                    "https://www.perplexity.ai/search/%2e%2e", "https://www.perplexity.ai/search/../x",
                    "https://www.perplexity.ai/search/x\n", "https://www.perplexity.ai/account"):
            with self.subTest(url=url), self.assertRaises(e.ExportError):
                e.parse_thread_url(url)

    def test_uuid_url_end_to_end_with_fake_authenticated_api(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); cookie = root / "cookie.txt"; cookie.write_text("fixture=fake")
            with patch.object(e, "Client", return_value=QueueClient([detail()])), contextlib.redirect_stdout(io.StringIO()):
                status = e.main(["-c", str(cookie), "-o", str(root / "out"), "--thread-url", f"https://www.perplexity.ai/search/{UID}"])
            self.assertEqual(status, 0)
            self.assertIn("Question 1", (root / "out" / "markdown" / f"chat_{UID}.md").read_text())


class SmokeTests(unittest.TestCase):
    def run_smoke(self, expected, text):
        with tempfile.TemporaryDirectory() as td:
            cookie = Path(td) / "cookies.txt"; cookie.write_text("fixture=fake")
            with patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}), patch.object(e, "Client", return_value=QueueClient([detail()])), contextlib.redirect_stdout(io.StringIO()):
                return live_verify.main(["--thread-id", UID, "--cookies", str(cookie), "--expected-turns", str(expected), "--expect-text", text])

    def test_smoke_pass_requires_expected_count_and_text(self):
        self.assertEqual(self.run_smoke(1, "Question 1"), 0)

    def test_smoke_fails_on_wrong_count(self):
        self.assertEqual(self.run_smoke(2, "Question 1"), 1)

    def test_smoke_fails_on_missing_text(self):
        self.assertEqual(self.run_smoke(1, "This is not present"), 1)

    def test_live_smoke_refuses_github_actions(self):
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as err:
            live_verify.main(["--thread-id", UID, "--expected-turns", "1", "--expect-text", "test"])
        self.assertEqual(err.exception.code, 2)


class WorkflowTests(unittest.TestCase):
    def test_public_workflow_is_a_secret_free_native_export_canary(self):
        workflow = (Path(__file__).parent / ".github" / "workflows" / "public-thread.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", workflow)
        self.assertIn("--native-export", workflow)
        self.assertIn("--output \"$RUNNER_TEMP/perplexity-public-export.md\"", workflow)
        self.assertIn("if: always()", workflow)
        self.assertIn("65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d", workflow)
        self.assertIn("c5e710abce41a79780e0d010e2123f5a55707121628f1667e99cb3497f89f78f", workflow)
        self.assertNotIn("expected_turns:", workflow)
        self.assertNotIn("expected_text:", workflow)
        self.assertNotIn("pplx_cookies", workflow.lower())
        self.assertNotIn("secrets.", workflow.lower())
        self.assertNotIn("upload-artifact", workflow)

    def _workflow_texts(self):
        root = Path(__file__).parent / ".github" / "workflows"
        return {path.name: path.read_text(encoding="utf-8") for path in sorted(root.glob("*.yml"))}

    def test_workflows_forbid_pull_request_target_and_secrets(self):
        for name, workflow in self._workflow_texts().items():
            with self.subTest(workflow=name):
                self.assertNotIn("pull_request_target", workflow)
                self.assertNotIn("secrets.", workflow)

    def test_actions_uses_are_sha_pinned_with_version_comment(self):
        pin = re.compile(r"^\s*-\s*uses:\s+actions/\S+@[0-9a-f]{40}\s+#\s+v\S+\s*$")
        for name, workflow in self._workflow_texts().items():
            for lineno, line in enumerate(workflow.splitlines(), 1):
                if "uses: actions/" not in line:
                    continue
                with self.subTest(workflow=name, line=lineno, text=line.strip()):
                    self.assertRegex(line, pin)

    def test_checkout_steps_set_persist_credentials_false(self):
        for name, workflow in self._workflow_texts().items():
            lines = workflow.splitlines()
            for lineno, line in enumerate(lines):
                if "uses: actions/checkout@" not in line:
                    continue
                window = "\n".join(lines[lineno:lineno + 8])
                with self.subTest(workflow=name, line=lineno + 1):
                    self.assertIn("persist-credentials: false", window)

    def test_ci_security_release_workflows_keep_hardening_markers(self):
        texts = self._workflow_texts()
        for required in ("ci.yml", "security.yml", "release.yml"):
            self.assertIn(required, texts)
        self.assertIn("workflow_call:", texts["ci.yml"])
        self.assertIn("workflow_call:", texts["security.yml"])
        self.assertIn("uses: ./.github/workflows/ci.yml", texts["release.yml"])
        self.assertIn("uses: ./.github/workflows/security.yml", texts["release.yml"])
        self.assertIn("uses: ./.github/workflows/resilience.yml", texts["release.yml"])
        self.assertIn("needs: [ci, security, resilience]", texts["release.yml"])
        # Dependency-review job is documented in CI.md for apply when workflow-scope
        # credentials are available; when present it must stay PR-only and SHA-pinned.
        sec = texts["security.yml"]
        if "actions/dependency-review-action@" in sec:
            self.assertIn("if: github.event_name == 'pull_request'", sec)
            self.assertRegex(
                sec,
                r"uses:\s+actions/dependency-review-action@[0-9a-f]{40}\s+#\s+v\S+",
            )


class PackageTests(unittest.TestCase):
    def test_package_is_reproducible_and_excludes_cookies(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name in FILES:
                path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("fixture")
            (root / "pplx_cookies.txt").write_text("DO-NOT-PACK")
            a = build(root, root / "one"); b = build(root, root / "two")
            self.assertEqual(a.read_bytes(), b.read_bytes())
            with zipfile.ZipFile(a) as package:
                self.assertEqual(set(package.namelist()), {"perplexity_export/" + n for n in FILES})
            self.assertTrue((a.parent / "SHA256SUMS.txt").is_file())
            checksum = (a.parent / "SHA256SUMS.txt").read_text(encoding="utf-8")
            self.assertEqual(checksum, f"{hashlib.sha256(a.read_bytes()).hexdigest()}  {a.name}\n")

    def test_actual_package_can_be_built(self):
        with tempfile.TemporaryDirectory() as td:
            archive = build(Path(__file__).resolve().parent, Path(td))
            with zipfile.ZipFile(archive) as package:
                self.assertIsNone(package.testzip())
                self.assertIn("perplexity_export/live_verify.py", package.namelist())


if __name__ == "__main__":
    unittest.main()
