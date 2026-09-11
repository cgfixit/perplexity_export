"""Unit tests for slug decode, native filename rules, and DOM helpers (no browser)."""
import unittest
from unittest.mock import patch

import pplx_export as e
import public_verify
import dom_export


PART1_SLUG = "trade-war-liberation-day-analy-rc__mHo7Qh6QT0SdaCAVzQ"
PART1_UUID = "adcfff98-7a3b-421e-904f-449d682015cd"
PART2_SLUG = "global-fair-trade-4-merica-mem-uHgvJvtxQheh22IYWXy_lQ"
PART2_UUID = "b8782f26-fb71-4217-a1db-6218597cbf95"


class SlugDecodeTests(unittest.TestCase):
    def test_known_share_slugs_decode_to_uuid(self):
        self.assertEqual(e.decode_share_slug_uuid(PART1_SLUG), PART1_UUID)
        self.assertEqual(e.decode_share_slug_uuid(PART2_SLUG), PART2_UUID)

    def test_resolve_accepts_uuid_and_slug_urls(self):
        self.assertEqual(
            e.resolve_share_thread_id(f"https://www.perplexity.ai/search/{PART1_UUID}"),
            PART1_UUID,
        )
        self.assertEqual(
            e.resolve_share_thread_id(f"https://www.perplexity.ai/search/{PART1_SLUG}"),
            PART1_UUID,
        )

    def test_undecodable_slug_and_foreign_host_rejected(self):
        with self.assertRaises(e.ExportError):
            e.resolve_share_thread_id("https://www.perplexity.ai/search/a-title-slug")
        with self.assertRaises(e.ExportError):
            e.resolve_share_thread_id(f"https://example.test/search/{PART1_UUID}")

    def test_non_slug_selector_returns_none(self):
        self.assertIsNone(e.decode_share_slug_uuid(PART1_UUID))
        self.assertIsNone(e.decode_share_slug_uuid("short"))


class NativeFilenameTests(unittest.TestCase):
    def test_allows_slash_in_title_but_rejects_traversal(self):
        self.assertTrue(public_verify.native_filename_is_safe("Trade War/Liberation Day.md"))
        self.assertTrue(public_verify.native_filename_is_safe("ordinary-title.md"))
        self.assertFalse(public_verify.native_filename_is_safe("../thread.md"))
        self.assertFalse(public_verify.native_filename_is_safe("foo/../../etc/passwd.md"))
        self.assertFalse(public_verify.native_filename_is_safe("/etc/passwd.md"))
        self.assertFalse(public_verify.native_filename_is_safe("thread.txt"))
        self.assertFalse(public_verify.native_filename_is_safe("bad\nname.md"))

    def test_native_export_accepts_slash_title_payload(self):
        import base64
        import contextlib
        import io
        import tempfile
        from pathlib import Path
        from test_export import QueueClient, UID
        from unittest.mock import patch

        data = b"# Body\n\nok\n"
        payload = {
            "filename": "Trade War/Liberation Day.md",
            "file_content_64": base64.b64encode(data).decode("ascii"),
        }
        client = QueueClient([payload])
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "saved.md"
            with patch.object(e, "Client", return_value=client), contextlib.redirect_stdout(output):
                status = public_verify.main([
                    "--thread-url", f"https://www.perplexity.ai/search/{UID}",
                    "--native-export", "--output", str(out),
                ])
            self.assertEqual(status, 0, output.getvalue())
            self.assertEqual(out.read_bytes(), data)
        self.assertIn("PASS:", output.getvalue())


class DomHelperTests(unittest.TestCase):
    def test_markdown_includes_first_user_prompt_and_report_fields(self):
        turns = [
            {"role": "user", "text": "Trade War/Liberation Day: analyze this image and phase 1",
             "attachments": ["image.jpg"]},
            {"role": "assistant", "text": "Based on the chart…", "attachments": []},
        ]
        report = {
            "access": "public",
            "turns_seen": 2,
            "scroll_exhausted": True,
            "ui_truncation_banner": "Sorry, could not load the rest of the thread",
            "continuation_urls": ["https://cgfixit.com/trade2.html"],
            "title": "Trade War/Liberation Day",
        }
        md = dom_export.turns_to_markdown(turns, report)
        self.assertIn("analyze this image", md)
        self.assertIn("image.jpg", md)
        self.assertIn("cgfixit.com/trade2.html", md)
        self.assertIn("could not load the rest", md)
        self.assertIn("turns_seen=2", md)

    def test_access_classification(self):
        self.assertEqual(dom_export._classify_access("This session is private — Sign in", []), "private")
        self.assertEqual(dom_export._classify_access("Sign in if you are the owner of this session, or to request access", []), "private")
        self.assertEqual(dom_export._classify_access("Viewing a shared session.", [{"role": "user"}]), "public")
        self.assertEqual(dom_export._classify_access("Please sign in to continue", []), "denied")

    def test_import_without_playwright_stays_usable(self):
        # Helper surface must not import playwright at module import time.
        self.assertTrue(callable(dom_export.turns_to_markdown))
        self.assertTrue(callable(dom_export.clean_text))


class DomLiveTests(unittest.TestCase):
    """Optional live smoke; skipped unless DOM_EXPORT_LIVE=1 and Playwright works."""

    @classmethod
    def setUpClass(cls):
        import os
        cls.enabled = os.environ.get("DOM_EXPORT_LIVE") == "1"
        if not cls.enabled:
            return
        try:
            import playwright  # noqa: F401
        except ImportError:
            cls.enabled = False

    def test_part1_opener_present(self):
        if not self.enabled:
            self.skipTest("Set DOM_EXPORT_LIVE=1 to run live DOM checks")
        md, report = dom_export.export_share(
            f"https://www.perplexity.ai/search/{PART1_UUID}"
        )
        self.assertEqual(report["access"], "public")
        lowered = md.lower()
        self.assertTrue(
            "liberation day" in lowered or "analyze this image" in lowered or "phase 1" in lowered,
            msg="Part 1 opener missing from DOM markdown",
        )
        self.assertGreater(report["turns_seen"], 0)


if __name__ == "__main__":
    unittest.main()
