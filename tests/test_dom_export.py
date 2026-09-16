"""Unit tests for slug decode, native filename rules, and DOM helpers (no browser)."""
import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pplx_export as e
import public_verify
import dom_export


PART1_SLUG = "trade-war-liberation-day-analy-rc__mHo7Qh6QT0SdaCAVzQ"
PART1_UUID = "adcfff98-7a3b-421e-904f-449d682015cd"
PART2_SLUG = "global-fair-trade-4-merica-mem-uHgvJvtxQheh22IYWXy_lQ"
PART2_UUID = "b8782f26-fb71-4217-a1db-6218597cbf95"


def dom_turn(token, role, text, top, *, attachments=(), copy_ready=False,
             markdown_features=None):
    turn = {
        "token": token,
        "identity": None,
        "role": role,
        "text": text,
        "attachments": list(attachments),
        "top": top,
        "height": 100,
        "copy_ready": copy_ready,
    }
    if markdown_features is not None:
        turn["markdown_features"] = markdown_features
    return turn


def dom_snapshot(turns, *, top=0, bottom=False, truncation=None, continuations=()):
    return {
        "turns": turns,
        "metrics": {"top": top, "height": 1000, "client": 300},
        "at_bottom": bottom,
        "loading": False,
        "truncation": truncation,
        "continuation_candidates": list(continuations),
        "access": "public",
    }


class FakeDomPage:
    def __init__(self, snapshots, copies=None, failures=(), endless=False):
        self.snapshots = snapshots
        self.copies = copies or {}
        self.failures = set(failures)
        self.endless = endless
        self.index = 0
        self.copy_calls = []
        self.waits = []
        self.clock = 0.0

    def reset_top(self):
        return True

    def expand(self, pace_ms):
        return 0

    def snapshot(self):
        return copy.deepcopy(self.snapshots[self.index])

    def throttle(self, pace_ms):
        self.waits.append(pace_ms)
        self.clock += pace_ms / 1000

    def monotonic(self):
        return self.clock

    def scroll_overlap(self):
        if self.index + 1 < len(self.snapshots):
            self.index += 1
            return True
        return self.endless

    def copy_markdown(self, turn, timeout_ms):
        token = turn["token"]
        self.copy_calls.append(token)
        if token in self.failures:
            raise dom_export.DomExportError("fixture copy failed")
        value = self.copies[token]
        return value.pop(0) if isinstance(value, list) else value


class TimedFakeDomPage(FakeDomPage):
    def throttle(self, pace_ms):
        super().throttle(pace_ms)
        if self.index + 1 < len(self.snapshots):
            self.index += 1

    def scroll_overlap(self):
        return False


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
    def test_markdown_copy_must_cover_visible_answer_content(self):
        features = {"headings": 1}
        dom_text = "Full answer heading\ncritical tail"
        dom_export._validate_markdown_payload(
            "## Full answer heading\n\ncritical tail", features, dom_text,
        )
        with self.assertRaisesRegex(dom_export.DomExportError, "omitted visible"):
            dom_export._validate_markdown_payload(
                "## Full answer heading", features, dom_text,
            )
        with self.assertRaisesRegex(dom_export.DomExportError, "omitted visible"):
            dom_export._validate_markdown_payload(
                "## Report\n\n[source](https://perplexity.ai/critical/tail)",
                {"headings": 1, "links": ["https://perplexity.ai/critical/tail"]},
                "Report\ncritical tail\nsource",
            )

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

    def test_live_page_url_must_stay_on_the_same_perplexity_thread(self):
        dom_export._validate_live_page_url(
            f"https://www.perplexity.ai/search/{PART1_UUID}", PART1_UUID,
        )
        dom_export._validate_live_page_url(
            f"https://www.perplexity.ai/search/{PART1_SLUG}", PART1_UUID,
        )
        with self.assertRaises(dom_export.DomExportError):
            dom_export._validate_live_page_url(
                f"https://www.perplexity.ai/search/{PART2_UUID}", PART1_UUID,
            )
        with self.assertRaises(dom_export.DomExportError):
            dom_export._validate_live_page_url(
                f"https://evil.example/search/{PART1_UUID}", PART1_UUID,
            )
        with self.assertRaises(dom_export.DomExportError):
            dom_export._validate_live_page_url(
                f"https://[www.perplexity.ai/search/{PART1_UUID}", PART1_UUID,
            )

    def test_navigation_guard_refuses_redirects_and_foreign_targets(self):
        class Page:
            main_frame = object()

            def route(self, _pattern, handler):
                self.handler = handler

        class Request:
            def __init__(self, url, frame):
                self.url = url
                self.frame = frame

            def is_navigation_request(self):
                return True

        class Route:
            def __init__(self, status):
                self.response = type("Response", (), {"status": status})()
                self.actions = []

            def fetch(self, **kwargs):
                self.actions.append(("fetch", kwargs))
                return self.response

            def abort(self):
                self.actions.append(("abort", None))

            def fulfill(self, **kwargs):
                self.actions.append(("fulfill", kwargs))

            def continue_(self):
                self.actions.append(("continue", None))

        page = Page()
        _handler, state = dom_export._install_navigation_guard(page, PART1_UUID)
        allowed = f"https://www.perplexity.ai/search/{PART1_UUID}"

        redirect = Route(302)
        page.handler(redirect, Request(allowed, page.main_frame))
        self.assertEqual([action for action, _ in redirect.actions], ["fetch", "abort"])
        self.assertTrue(state["blocked"])

        _handler, state = dom_export._install_navigation_guard(page, PART1_UUID)
        success = Route(200)
        page.handler(success, Request(allowed, page.main_frame))
        self.assertEqual([action for action, _ in success.actions], ["fetch", "fulfill"])
        self.assertFalse(state["blocked"])

        foreign = Route(200)
        page.handler(foreign, Request("https://evil.example/", page.main_frame))
        self.assertEqual([action for action, _ in foreign.actions], ["abort"])
        self.assertTrue(state["blocked"])

    def test_dedicated_profile_requires_tool_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile"
            profile.mkdir()
            (profile / "personal-data").write_text("untouched", encoding="utf-8")
            with self.assertRaises(dom_export.DomExportError):
                dom_export._prepare_profile_dir(profile)
            self.assertEqual((profile / "personal-data").read_text(encoding="utf-8"), "untouched")

            empty = root / "dedicated"
            prepared = dom_export._prepare_profile_dir(empty)
            marker = prepared / dom_export.PROFILE_MARKER
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_text(encoding="utf-8"), dom_export.PROFILE_MARKER_TEXT)
            self.assertEqual(dom_export._prepare_profile_dir(empty), prepared)
            if os.name == "posix":
                self.assertEqual(prepared.stat().st_mode & 0o777, 0o700)


class DomCollectorTests(unittest.TestCase):
    def base_report(self):
        return {
            "access": "denied",
            "turns_seen": 0,
            "scroll_exhausted": False,
            "ui_truncation_banner": None,
            "continuation_urls": [],
            "title": "Fixture",
        }

    def test_virtualized_collection_preserves_order_duplicates_markdown_and_metadata(self):
        prefix = "x" * 240
        snapshots = [
            dom_snapshot([
                dom_turn("u1", "user", prefix + " one", 0, attachments=["image.jpg"]),
                dom_turn("a1", "assistant", "answer one", 100, copy_ready=True),
                dom_turn("u2", "user", "repeat prompt", 200),
            ], truncation="Sorry, could not load the rest of the thread",
               continuations=["https://cgfixit.com/trade2.html", "https://evilcgfixit.com/no"]),
            dom_snapshot([
                dom_turn("a1", "assistant", "answer one", 100, copy_ready=True),
                dom_turn("u2", "user", "repeat prompt", 200),
                dom_turn("a2", "assistant", "same answer", 300, copy_ready=True),
                dom_turn("u3", "user", prefix + " two", 400),
            ], top=150),
            dom_snapshot([
                dom_turn("a2", "assistant", "same answer", 300, copy_ready=True),
                dom_turn("u3", "user", prefix + " two", 400),
                dom_turn("a3", "assistant", "done", 500, copy_ready=True),
                dom_turn("u4", "user", "repeat prompt", 600),
                dom_turn("a4", "assistant", "same answer", 700, copy_ready=True),
            ], top=700, bottom=True),
        ]
        same_markdown = "## Same\n\n[link](https://example.test)\n\n| A | B |\n| - | - |"
        copies = {
            "a1": "## Answer one\n\n[primary](https://example.test)",
            "a2": same_markdown,
            "a3": "```python\nprint('done')\n```",
            "a4": same_markdown,
        }
        page = FakeDomPage(snapshots, copies)
        checkpoints = []
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=20,
            max_steps=30, checkpoint=lambda markdown, data: checkpoints.append((markdown, data)),
        )
        turns = dom_export._public_turns(records)

        self.assertEqual(
            [(turn["role"], turn["text"]) for turn in turns],
            [
                ("user", prefix + " one"),
                ("assistant", copies["a1"]),
                ("user", "repeat prompt"),
                ("assistant", same_markdown),
                ("user", prefix + " two"),
                ("assistant", copies["a3"]),
                ("user", "repeat prompt"),
                ("assistant", same_markdown),
            ],
        )
        self.assertEqual(page.copy_calls, ["a1", "a2", "a3", "a4"])
        self.assertEqual(turns[0]["attachments"], ["image.jpg"])
        self.assertEqual(report["continuation_urls"], ["https://cgfixit.com/trade2.html"])
        self.assertEqual(report["stop_reason"], "ui_truncated")
        self.assertEqual(report["status"], "partial")
        self.assertTrue(report["scroll_exhausted"])
        self.assertTrue(checkpoints)

    def test_copy_failure_and_step_limit_are_explicit_partial_results(self):
        snapshot = dom_snapshot([
            dom_turn("u1", "user", "question", 0),
            dom_turn("a1", "assistant", "answer", 100, copy_ready=True),
        ], bottom=True)
        failed_page = FakeDomPage([snapshot], copies={"a1": "unused"}, failures={"a1"})
        _records, failed = dom_export._collect(
            failed_page, self.base_report(), pace_ms=1,
            settle_timeout_ms=20, max_steps=10,
        )
        self.assertEqual(failed["status"], "partial")
        self.assertEqual(failed["stop_reason"], "copy_failed")
        self.assertEqual(failed_page.copy_calls, ["a1", "a1"])

        capped_page = FakeDomPage([
            dom_snapshot([dom_turn("u1", "user", "question", 0)])
        ], endless=True)
        _records, capped = dom_export._collect(
            capped_page, self.base_report(), pace_ms=1,
            settle_timeout_ms=20, max_steps=3,
        )
        self.assertEqual(capped["status"], "partial")
        self.assertEqual(capped["stop_reason"], "step_limit")

    def test_delayed_content_settles_before_copy_and_keeps_attachment(self):
        snapshots = [
            {**dom_snapshot([
                dom_turn("u1", "user", "question", 0),
                dom_turn("a1", "assistant", "", 100),
            ], bottom=True), "loading": True},
            {**dom_snapshot([
                dom_turn("u1", "user", "question", 0),
                dom_turn("a1", "assistant", "partial", 100),
            ], bottom=True), "loading": True},
            dom_snapshot([
                dom_turn("u1", "user", "question", 0, attachments=["image.jpg"]),
                dom_turn("a1", "assistant", "final answer", 100, copy_ready=True),
            ], bottom=True),
        ]
        page = TimedFakeDomPage(snapshots, copies={"a1": "## Final answer"})
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1,
            settle_timeout_ms=20, max_steps=20,
        )
        turns = dom_export._public_turns(records)
        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(page.copy_calls, ["a1"])
        self.assertEqual(turns[0]["attachments"], ["image.jpg"])
        self.assertEqual(turns[1]["text"], "## Final answer")

    def test_ambiguous_repeated_overlap_fails_closed(self):
        first = dom_snapshot([
            dom_turn("u1", "user", "same question", 0),
            dom_turn("a1", "assistant", "same answer", 100, copy_ready=True),
            dom_turn("u2", "user", "same question", 200),
            dom_turn("a2", "assistant", "same answer", 300, copy_ready=True),
        ])
        recycled = dom_snapshot([
            dom_turn("u3", "user", "same question", 400),
            dom_turn("a3", "assistant", "same answer", 500, copy_ready=True),
            dom_turn("u4", "user", "same question", 600),
            dom_turn("a4", "assistant", "same answer", 700, copy_ready=True),
        ], top=400, bottom=True)
        page = FakeDomPage([first, recycled], copies={
            "a1": "same answer", "a2": "same answer",
            "a3": "same answer", "a4": "same answer",
        })
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1,
            settle_timeout_ms=20, max_steps=20,
        )
        self.assertEqual(len(records), 4)
        self.assertEqual(report["stop_reason"], "ambiguous_overlap")
        self.assertEqual(report["status"], "partial")

    def test_access_loss_after_collection_is_not_reported_as_initial_auth(self):
        first = dom_snapshot([
            dom_turn("u1", "user", "question", 0),
            dom_turn("a1", "assistant", "answer", 100, copy_ready=True),
        ])
        denied = {**dom_snapshot([], top=200), "access": "denied"}
        page = FakeDomPage([first, denied], copies={"a1": "answer"})
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1,
            settle_timeout_ms=20, max_steps=20,
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(report["stop_reason"], "access_lost")
        self.assertEqual(report["status"], "partial")

    def test_remounted_attachment_metadata_is_monotonic(self):
        snapshots = [
            dom_snapshot([
                dom_turn("u1", "user", "question", 0, attachments=["image.jpg"]),
                dom_turn("a1", "assistant", "answer", 100, copy_ready=True),
            ], bottom=True),
            dom_snapshot([
                dom_turn("u1", "user", "question", 0),
                dom_turn("a1", "assistant", "answer", 100, copy_ready=True),
            ], bottom=True),
        ]
        page = TimedFakeDomPage(snapshots, copies={"a1": "answer"})
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=100, max_steps=20,
        )
        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(dom_export._public_turns(records)[0]["attachments"], ["image.jpg"])

    def test_initial_banner_and_continuation_survive_the_first_paced_wait(self):
        turns = [
            dom_turn("u1", "user", "question", 0),
            dom_turn("a1", "assistant", "answer", 100, copy_ready=True),
        ]
        initial = dom_snapshot(
            turns,
            bottom=True,
            truncation="Sorry, could not load the rest of the thread",
            continuations=("https://cgfixit.com/trade2.html",),
        )
        settled = dom_snapshot(turns, bottom=True)
        page = TimedFakeDomPage([initial, settled, settled], copies={"a1": "answer"})
        _records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=100, max_steps=20,
        )
        self.assertEqual(report["stop_reason"], "ui_truncated")
        self.assertEqual(
            report["ui_truncation_banner"],
            "Sorry, could not load the rest of the thread",
        )
        self.assertEqual(report["continuation_urls"], ["https://cgfixit.com/trade2.html"])

    def test_transient_unmounted_turn_is_retained_as_explicit_partial(self):
        snapshots = [
            dom_snapshot([
                dom_turn("u1", "user", "question", 0),
                dom_turn("a1", "assistant", "answer", 100, copy_ready=True),
                dom_turn("u2", "user", "transient question", 200),
            ], bottom=True),
            dom_snapshot([
                dom_turn("u1", "user", "question", 0),
                dom_turn("a1", "assistant", "answer", 100, copy_ready=True),
            ], bottom=True),
        ]
        page = TimedFakeDomPage(snapshots, copies={"a1": "answer"})
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=100, max_steps=20,
        )
        self.assertEqual([record["_dom_text"] for record in records], [
            "question", "answer", "transient question",
        ])
        self.assertEqual(report["stop_reason"], "transient_turn_unresolved")
        self.assertEqual(report["status"], "partial")

    def test_same_node_same_position_can_finish_after_false_quiet(self):
        partial = dom_snapshot([
            dom_turn("u1", "user", "question", 0),
            dom_turn("a1", "assistant", "partial", 100, copy_ready=True),
        ], bottom=True)
        final = dom_snapshot([
            dom_turn("u1", "user", "question", 0),
            dom_turn("a1", "assistant", "final", 100, copy_ready=True),
        ], bottom=True)
        page = TimedFakeDomPage(
            [partial, partial, final, final], copies={"a1": ["partial", "final"]},
        )
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=100, max_steps=30,
        )
        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(dom_export._public_turns(records)[1]["text"], "final")
        self.assertEqual(page.copy_calls, ["a1", "a1"])

    def test_recycled_node_with_new_position_cannot_overwrite_prior_turn(self):
        first = dom_snapshot([
            dom_turn("u1", "user", "first question", 0),
            dom_turn("a1", "assistant", "first answer", 100, copy_ready=True),
        ])
        recycled = dom_snapshot([
            dom_turn("u1", "user", "second question", 200),
            dom_turn("a1", "assistant", "second answer", 300, copy_ready=True),
        ], top=200, bottom=True)
        page = FakeDomPage([first, recycled], copies={"a1": "first answer"})
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=100, max_steps=20,
        )
        self.assertEqual([record["_dom_text"] for record in records], [
            "first question", "first answer",
        ])
        self.assertEqual(report["stop_reason"], "lost_overlap")

    def test_recycled_identical_nodes_at_new_positions_cannot_hide_repeated_pair(self):
        first = dom_snapshot([
            dom_turn("u1", "user", "same question", 0),
            dom_turn("a1", "assistant", "same answer", 100, copy_ready=True),
        ])
        recycled = dom_snapshot([
            dom_turn("u1", "user", "same question", 200),
            dom_turn("a1", "assistant", "same answer", 300, copy_ready=True),
            dom_turn("u2", "user", "trailing question", 400),
        ], top=200, bottom=True)
        page = FakeDomPage([first, recycled], copies={"a1": "same answer"})
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=100, max_steps=20,
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(report["stop_reason"], "ambiguous_overlap")

    def test_late_markdown_features_force_a_fresh_copy(self):
        plain = dom_snapshot([
            dom_turn("u1", "user", "question", 0),
            dom_turn("a1", "assistant", "answer", 100, copy_ready=True),
        ])
        hydrated = dom_snapshot([
            dom_turn("u1", "user", "question", 0),
            dom_turn(
                "a1", "assistant", "answer", 100, copy_ready=True,
                markdown_features={
                    "links": ["https://example.test/"],
                    "code_blocks": 0,
                    "tables": 0,
                    "headings": 0,
                },
            ),
        ], bottom=True)
        page = FakeDomPage(
            [plain, hydrated],
            copies={"a1": ["answer", "[answer](https://example.test/)"]},
        )
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=100, max_steps=20,
        )
        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(page.copy_calls, ["a1", "a1"])
        self.assertEqual(
            dom_export._public_turns(records)[1]["text"],
            "[answer](https://example.test/)",
        )

    def test_unmounted_late_markdown_features_are_explicitly_partial(self):
        first = dom_snapshot([
            dom_turn("u1", "user", "question 1", 0),
            dom_turn("a1", "assistant", "answer 1", 100, copy_ready=True),
            dom_turn("u2", "user", "question 2", 200),
            dom_turn("a2", "assistant", "answer 2", 300, copy_ready=True),
        ])
        late_feature = dom_snapshot([
            dom_turn(
                "a1", "assistant", "answer 1", 100,
                markdown_features={"links": ["https://example.test/"]},
            ),
            dom_turn("u2", "user", "question 2", 200),
            dom_turn("a2", "assistant", "answer 2", 300, copy_ready=True),
            dom_turn("u3", "user", "question 3", 400),
            dom_turn("a3", "assistant", "answer 3", 500, copy_ready=True),
        ], top=100, bottom=True)
        after_unmount = dom_snapshot([
            dom_turn("u2", "user", "question 2", 200),
            dom_turn("a2", "assistant", "answer 2", 300, copy_ready=True),
            dom_turn("u3", "user", "question 3", 400),
            dom_turn("a3", "assistant", "answer 3", 500, copy_ready=True),
        ], top=200, bottom=True)

        class UnmountingPage(FakeDomPage):
            def __init__(self):
                super().__init__(
                    [first, late_feature, after_unmount],
                    copies={"a1": "answer 1", "a2": "answer 2", "a3": "answer 3"},
                )
                self.late_waits = 0

            def throttle(self, pace_ms):
                super().throttle(pace_ms)
                if self.index == 1:
                    self.late_waits += 1
                    if self.late_waits == 2:
                        self.index = 2

        page = UnmountingPage()
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=100, max_steps=30,
        )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["stop_reason"], "transient_turn_unresolved")
        self.assertEqual(dom_export._public_turns(records)[1]["capture"], "dom_text_fallback")
        self.assertIn("unmounted", report["copy_failures"][0]["reason"])

    def test_recycled_staged_nodes_cannot_erase_unsettled_turns(self):
        first = dom_snapshot([
            dom_turn("u1", "user", "question 1", 0),
            dom_turn("a1", "assistant", "answer 1", 100, copy_ready=True),
            dom_turn("u2", "user", "question 2", 200),
            dom_turn("a2", "assistant", "answer 2", 300, copy_ready=True),
        ])
        third_pair = dom_snapshot([
            dom_turn("u2", "user", "question 2", 200),
            dom_turn("a2", "assistant", "answer 2", 300, copy_ready=True),
            dom_turn("reused-u", "user", "question 3", 400),
            dom_turn("reused-a", "assistant", "answer 3", 500, copy_ready=True),
        ], top=200, bottom=True)
        recycled_pair = dom_snapshot([
            dom_turn("u2", "user", "question 2", 200),
            dom_turn("a2", "assistant", "answer 2", 300, copy_ready=True),
            dom_turn("reused-u", "user", "question 4", 600),
            dom_turn("reused-a", "assistant", "answer 4", 700, copy_ready=True),
        ], top=200, bottom=True)

        class RecyclingPage(FakeDomPage):
            def __init__(self):
                super().__init__(
                    [first, third_pair, recycled_pair],
                    copies={
                        "a1": "answer 1",
                        "a2": "answer 2",
                        "reused-a": "answer 4",
                    },
                )
                self.recycle_waits = 0

            def throttle(self, pace_ms):
                super().throttle(pace_ms)
                if self.index == 1:
                    self.recycle_waits += 1
                    if self.recycle_waits == 2:
                        self.index = 2

        records, report = dom_export._collect(
            RecyclingPage(), self.base_report(),
            pace_ms=1, settle_timeout_ms=100, max_steps=30,
        )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["stop_reason"], "transient_turn_unresolved")
        self.assertIn("question 3", [record["_dom_text"] for record in records])

    def test_bottom_requires_quiet_dwell_before_declaring_completion(self):
        partial = dom_snapshot([
            dom_turn("u1", "user", "question", 0),
            dom_turn("a1", "assistant", "partial", 100, copy_ready=True),
        ], bottom=True)
        final = dom_snapshot([
            dom_turn("u1", "user", "question", 0),
            dom_turn("a1", "assistant", "final", 100, copy_ready=True),
        ], bottom=True)
        page = TimedFakeDomPage(
            [partial, partial, partial, partial, partial, final, final],
            copies={"a1": ["partial", "final"]},
        )
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=750,
            settle_timeout_ms=15000, max_steps=30,
        )
        self.assertEqual(report["status"], "complete", report)
        self.assertEqual(dom_export._public_turns(records)[1]["text"], "final")

    def test_high_valid_pace_does_not_consume_the_settle_deadline(self):
        settled = dom_snapshot([
            dom_turn("u1", "user", "question", 0),
            dom_turn("a1", "assistant", "answer", 100, copy_ready=True),
        ], bottom=True)
        page = TimedFakeDomPage([settled], copies={"a1": "answer"})
        _records, report = dom_export._collect(
            page, self.base_report(), pace_ms=10000,
            settle_timeout_ms=15000, max_steps=20,
        )
        self.assertEqual(report["status"], "complete", report)
        self.assertTrue(report["scroll_exhausted"])

    def test_prompt_expansion_failure_is_explicit(self):
        class ExpansionFailure(FakeDomPage):
            def expand(self, pace_ms):
                raise dom_export.DomExportError("fixture expansion failed")

        page = ExpansionFailure([
            dom_snapshot([dom_turn("u1", "user", "truncated", 0)], bottom=True)
        ])
        _records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=20, max_steps=5,
        )
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["stop_reason"], "expansion_failed")
        self.assertEqual(report["expansion_failures"], ["fixture expansion failed"])

    def test_single_repeated_pair_with_new_nodes_is_ambiguous(self):
        first = dom_snapshot([
            dom_turn("u1", "user", "same question", 0),
            dom_turn("a1", "assistant", "same answer", 100, copy_ready=True),
        ])
        repeated = dom_snapshot([
            dom_turn("u2", "user", "same question", 200),
            dom_turn("a2", "assistant", "same answer", 300, copy_ready=True),
        ], top=200, bottom=True)
        page = FakeDomPage([first, repeated], copies={
            "a1": "same answer", "a2": "same answer",
        })
        records, report = dom_export._collect(
            page, self.base_report(), pace_ms=1, settle_timeout_ms=100, max_steps=20,
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(report["stop_reason"], "ambiguous_overlap")

    def test_incomplete_turn_sequences_never_report_complete(self):
        for turns, reason in (
            ([dom_turn("a1", "assistant", "orphan", 0, copy_ready=True)], "start_turn_missing"),
            ([dom_turn("u1", "user", "unanswered", 0)], "unfinished"),
        ):
            with self.subTest(reason=reason):
                page = FakeDomPage(
                    [dom_snapshot(turns, bottom=True)], copies={"a1": "orphan"},
                )
                _records, report = dom_export._collect(
                    page, self.base_report(), pace_ms=1,
                    settle_timeout_ms=100, max_steps=10,
                )
                self.assertEqual(report["status"], "partial")
                self.assertEqual(report["stop_reason"], reason)

    def test_public_pacing_floor_is_enforced_before_browser_start(self):
        with self.assertRaisesRegex(dom_export.DomExportError, "between 750 and 10000"):
            dom_export.export_share(
                f"https://www.perplexity.ai/search/{PART1_UUID}", pace_ms=749,
            )


class DomOutputRecoveryTests(unittest.TestCase):
    def report(self, status="partial"):
        return {
            "status": status,
            "stop_reason": None if status == "complete" else "copy_failed",
            "access": "public",
            "turns_seen": 2,
            "scroll_exhausted": status == "complete",
            "ui_truncation_banner": None,
            "continuation_urls": [],
        }

    def test_cli_rejects_output_inside_dedicated_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            error = io.StringIO()
            with patch.object(dom_export, "export_share") as export, contextlib.redirect_stderr(error):
                status = dom_export.main([
                    "--thread-url", f"https://www.perplexity.ai/search/{PART1_UUID}",
                    "--headed",
                    "--profile-dir", str(profile),
                    "--output", str(profile / "Default" / "Cookies"),
                ])
            self.assertEqual(status, 2)
            export.assert_not_called()
            self.assertIn("outside --profile-dir", error.getvalue())

    def test_partial_capture_preserves_prior_complete_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "share.md"
            report_path = Path(directory) / "share.json"
            output.write_text("prior complete\n", encoding="utf-8")
            stderr = io.StringIO()
            with patch.object(dom_export, "export_share", return_value=("partial\n", self.report())), \
                    contextlib.redirect_stderr(stderr):
                status = dom_export.main([
                    "--thread-url", f"https://www.perplexity.ai/search/{PART1_UUID}",
                    "--output", str(output), "--report", str(report_path),
                ])
            self.assertEqual(status, 1, stderr.getvalue())
            self.assertEqual(output.read_text(encoding="utf-8"), "prior complete\n")
            self.assertEqual((Path(directory) / "share.partial.md").read_text(encoding="utf-8"), "partial\n")
            partial_report = json.loads((Path(directory) / "share.partial.json").read_text(encoding="utf-8"))
            self.assertEqual(partial_report["stop_reason"], "copy_failed")

    def test_atomic_replacement_failure_preserves_prior_complete_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "share.md"
            output.write_text("prior complete\n", encoding="utf-8")
            stderr = io.StringIO()
            with patch.object(dom_export, "export_share", return_value=("new complete\n", self.report("complete"))), \
                    patch.object(e.os, "replace", side_effect=OSError("fixture interruption")), \
                    contextlib.redirect_stderr(stderr):
                status = dom_export.main([
                    "--thread-url", f"https://www.perplexity.ai/search/{PART1_UUID}",
                    "--output", str(output),
                ])
            self.assertEqual(status, 1, stderr.getvalue())
            self.assertEqual(output.read_text(encoding="utf-8"), "prior complete\n")

    def test_second_replacement_failure_rolls_back_both_final_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "share.md"
            report_path = Path(directory) / "share.json"
            output.write_text("prior complete\n", encoding="utf-8")
            report_path.write_text('{"old": true}\n', encoding="utf-8")
            real_replace = e.os.replace
            calls = 0

            def fail_second(source, destination):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("fixture second replacement interruption")
                return real_replace(source, destination)

            stderr = io.StringIO()
            with patch.object(dom_export, "export_share", return_value=(
                    "new complete\n", self.report("complete"))), \
                    patch.object(e.os, "replace", side_effect=fail_second), \
                    contextlib.redirect_stderr(stderr):
                status = dom_export.main([
                    "--thread-url", f"https://www.perplexity.ai/search/{PART1_UUID}",
                    "--output", str(output), "--report", str(report_path),
                ])
            self.assertEqual(status, 1, stderr.getvalue())
            self.assertEqual(output.read_text(encoding="utf-8"), "prior complete\n")
            self.assertEqual(report_path.read_text(encoding="utf-8"), '{"old": true}\n')

    def test_final_and_partial_output_paths_must_be_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "same.md"
            with patch.object(dom_export, "export_share") as export, \
                    contextlib.redirect_stderr(io.StringIO()):
                status = dom_export.main([
                    "--thread-url", f"https://www.perplexity.ai/search/{PART1_UUID}",
                    "--output", str(path), "--report", str(path),
                ])
            self.assertEqual(status, 2)
            export.assert_not_called()


class DomBrowserFixtureTests(unittest.TestCase):
    """Optional real-browser test against an offline virtualized page."""

    def test_virtualized_page_scrolls_and_copies_markdown(self):
        if os.environ.get("DOM_EXPORT_BROWSER_FIXTURE") != "1":
            self.skipTest("Set DOM_EXPORT_BROWSER_FIXTURE=1 to run the Playwright fixture")
        from playwright.sync_api import sync_playwright

        prefix = "x" * 240
        same_markdown = (
            "## same answer\n\n[link](https://example.test)\n\n"
            "| A | B |\n| - | - |\n| 1 | 2 |"
        )
        turns = [
            {"role": "user", "text": prefix + " one", "collapsed": "first prompt…", "attachment": "image.jpg"},
            {"role": "assistant", "text": "answer one", "markdown": "## Answer one\n\n[primary](https://example.test)", "render": "link", "write": "items"},
            {"role": "user", "text": "repeat prompt"},
            {"role": "assistant", "text": "same answer", "markdown": same_markdown, "render": "table"},
            {"role": "user", "text": prefix + " two", "collapsed": "later prompt…"},
            {"role": "assistant", "text": "done", "markdown": "```python\nprint('done')\n```", "render": "code"},
            {"role": "user", "text": "repeat prompt"},
            {"role": "assistant", "text": "same answer", "markdown": same_markdown, "render": "table"},
        ]
        fixture = """
            <style>
              .scrollable-container { height: 240px; overflow-y: scroll; position: relative; }
              #spacer { height: 800px; position: relative; }
              .turn { font-size: 10px; height: 90px; left: 0; position: absolute; right: 0; }
              .turn h2 { font-size: 11px; line-height: 12px; margin: 0; }
              .turn table { font-size: 8px; line-height: 8px; }
            </style>
            <p>Part 2 continuation: <a href="https://cgfixit.com/trade2.html">continue</a></p>
            <div class="scrollable-container"><div id="spacer"><div id="mount"></div></div></div>
            <script>
              const turns = __TURNS__;
              const root = document.querySelector('.scrollable-container');
              const mount = document.querySelector('#mount');
              let renderedStart = -1;
              function render() {
                const start = Math.min(turns.length - 3, Math.max(0, Math.floor(root.scrollTop / 100)));
                if (start === renderedStart) return;
                renderedStart = start;
                mount.replaceChildren();
                for (let index = start; index < Math.min(turns.length, start + 3); index++) {
                  const turn = turns[index];
                  const host = document.createElement('div');
                  host.className = `turn ${turn.role === 'user' ? 'group/user-bubble' : 'group/final-text'}`;
                  host.style.top = `${index * 100}px`;
                  const body = document.createElement('div');
                  if (turn.render === 'link' || turn.render === 'table') {
                    const heading = document.createElement('h2');
                    heading.textContent = turn.text;
                    const link = document.createElement('a');
                    link.href = 'https://example.test';
                    link.textContent = turn.render === 'link' ? 'primary' : 'link';
                    body.append(heading, link);
                    if (turn.render === 'table') {
                      const table = document.createElement('table');
                      table.innerHTML = '<tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr>';
                      body.append(table);
                    }
                  } else if (turn.render === 'code') {
                    const pre = document.createElement('pre');
                    const code = document.createElement('code');
                    code.textContent = turn.text;
                    const nestedCopy = document.createElement('button');
                    nestedCopy.setAttribute('aria-label', 'Copy');
                    pre.append(code, nestedCopy);
                    body.append(pre);
                  } else {
                    body.textContent = turn.text;
                  }
                  host.append(body);
                  if (turn.role === 'user' && turn.collapsed) {
                    body.textContent = turn.collapsed;
                    const expand = document.createElement('button');
                    expand.dataset.testid = 'toggle-query-expand-button';
                    expand.setAttribute('aria-label', 'Expand query');
                    expand.textContent = 'Expand';
                    expand.addEventListener('click', () => {
                      body.textContent = turn.text;
                      expand.remove();
                    });
                    host.append(expand);
                  }
                  if (turn.attachment) {
                    const image = document.createElement('img');
                    image.alt = turn.attachment;
                    host.append(image);
                  }
                  if (turn.role === 'assistant') {
                    const toolbar = document.createElement('div');
                    const copy = document.createElement('button');
                    copy.setAttribute('aria-label', 'Copy');
                    copy.textContent = 'Copy';
                    copy.addEventListener('click', () => {
                      if (turn.write === 'items') {
                        navigator.clipboard.write([{
                          types: ['text/plain', 'text/html'],
                          getType: async type => new Blob([
                            type === 'text/plain' ? turn.markdown : `<p>${turn.text}</p>`
                          ], {type}),
                        }]);
                      } else {
                        navigator.clipboard.writeText(turn.markdown);
                      }
                    });
                    toolbar.append(copy);
                    host.append(toolbar);
                  }
                  mount.append(host);
                }
              }
              root.addEventListener('scroll', render);
              render();
            </script>
        """.replace("__TURNS__", json.dumps(turns))

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 900, "height": 600})
                context.add_init_script(script=dom_export.ISOLATED_CLIPBOARD_SCRIPT)
                page = context.new_page()
                page.set_content(fixture)
                page.evaluate("value => navigator.clipboard.writeText(value)", "fixture-sentinel")
                self.assertEqual(
                    page.evaluate("() => navigator.clipboard.readText()"), "fixture-sentinel"
                )
                page.locator('button[aria-label="Copy"]').first.click()
                page.wait_for_function(
                    "() => window.__domExportClipboard.text !== 'fixture-sentinel'"
                )
                self.assertEqual(
                    page.evaluate("() => navigator.clipboard.readText()"), turns[1]["markdown"]
                )
                probe = dom_export._LivePage(page)
                probe.reset_top()
                probe_turn = next(
                    turn for turn in probe.snapshot()["turns"] if turn["role"] == "assistant"
                )
                self.assertEqual(probe.copy_markdown(probe_turn, 3000), turns[1]["markdown"])
                recycle_page = context.new_page()
                recycle_page.set_content("""
                    <style>.scrollable-container { height: 500px; overflow-y: auto; }</style>
                    <div class="scrollable-container">
                      <div class="group/user-bubble">recycle question</div>
                      <div class="group/final-text">
                        <div id="recycled-body">answer one</div>
                        <div><button aria-label="Copy"
                          onclick="navigator.clipboard.writeText('answer one')">Copy</button></div>
                      </div>
                    </div>
                """)
                recycle_probe = dom_export._LivePage(recycle_page)
                old_turn = next(
                    turn for turn in recycle_probe.snapshot()["turns"]
                    if turn["role"] == "assistant"
                )
                recycle_page.evaluate("""() => {
                    document.querySelector('#recycled-body').textContent = 'answer two';
                    document.querySelector('button').setAttribute(
                        'onclick', "navigator.clipboard.writeText('answer two')"
                    );
                }""")
                with self.assertRaisesRegex(dom_export.DomExportError, "changed before Copy"):
                    recycle_probe.copy_markdown(old_turn, 3000)
                adapter = dom_export._LivePage(page)
                records, report = dom_export._collect(
                    adapter, self.base_report(), pace_ms=1,
                    settle_timeout_ms=5000, max_steps=50,
                )
                short_page = context.new_page()
                short_page.set_content("""
                    <style>.scrollable-container { height: 500px; overflow-y: auto; }</style>
                    <div class="scrollable-container">
                      <div class="group/user-bubble">short question</div>
                      <div class="group/final-text">
                        <div>short answer</div>
                        <div><button aria-label="Copy"
                          onclick="navigator.clipboard.writeText('short answer')">Copy</button></div>
                      </div>
                    </div>
                """)
                short_records, short_report = dom_export._collect(
                    dom_export._LivePage(short_page), self.base_report(), pace_ms=1,
                    settle_timeout_ms=5000, max_steps=20,
                )
                html_page = context.new_page()
                html_page.set_content("""
                    <style>.scrollable-container { height: 500px; overflow-y: auto; }</style>
                    <div class="scrollable-container">
                      <div class="group/user-bubble">HTML-only question</div>
                      <div class="group/final-text">
                        <div>HTML-only answer</div>
                        <div><button aria-label="Copy">Copy</button></div>
                      </div>
                    </div>
                    <script>
                      document.querySelector('button').addEventListener('click', () => {
                        navigator.clipboard.write([{
                          types: ['text/html'],
                          getType: async () => new Blob(['<p>HTML-only answer</p>'], {type: 'text/html'}),
                        }]);
                      });
                    </script>
                """)
                html_records, html_report = dom_export._collect(
                    dom_export._LivePage(html_page), self.base_report(), pace_ms=1,
                    settle_timeout_ms=5000, max_steps=20,
                )
                truncated_copy_page = context.new_page()
                truncated_copy_page.set_content("""
                    <style>.scrollable-container { height: 500px; overflow-y: auto; }</style>
                    <div class="scrollable-container">
                      <div class="group/user-bubble">coverage question</div>
                      <div class="group/final-text">
                        <div><h2>Full answer heading</h2><p>critical tail</p></div>
                        <div><button aria-label="Copy"
                          onclick="navigator.clipboard.writeText('## Full answer heading')">Copy</button></div>
                      </div>
                    </div>
                """)
                truncated_records, truncated_report = dom_export._collect(
                    dom_export._LivePage(truncated_copy_page), self.base_report(), pace_ms=1,
                    settle_timeout_ms=5000, max_steps=20,
                )
                replacement_page = context.new_page()
                replacement_page.set_content("""
                    <div class="group/user-bubble">
                      <span>still truncated…</span>
                      <button data-testid="toggle-query-expand-button"
                        aria-label="Expand query">Expand</button>
                    </div>
                    <script>
                      document.querySelector('button').addEventListener('click', event => {
                        const replacement = event.currentTarget.cloneNode(true);
                        event.currentTarget.replaceWith(replacement);
                      });
                    </script>
                """)
                with self.assertRaisesRegex(dom_export.DomExportError, "remained truncated"):
                    dom_export._expand_truncated_queries(replacement_page, pace_ms=1)
                self.assertTrue(
                    replacement_page.locator('button[aria-label="Expand query"]').is_visible()
                )
                many_expands_page = context.new_page()
                many_expands_page.set_content("<main></main>")
                many_expands_page.evaluate("""() => {
                    const main = document.querySelector('main');
                    for (let index = 0; index < 51; index += 1) {
                        const bubble = document.createElement('div');
                        bubble.className = 'group/user-bubble';
                        const text = document.createElement('span');
                        text.textContent = index === 50 ? 'last truncated' : `already ${index}`;
                        const button = document.createElement('button');
                        button.dataset.testid = 'toggle-query-expand-button';
                        button.setAttribute('aria-label', 'Expand query');
                        if (index < 50) button.setAttribute('aria-expanded', 'true');
                        else button.addEventListener('click', () => {
                            text.textContent = 'last fully expanded';
                            button.remove();
                        });
                        bubble.append(text, button);
                        main.append(bubble);
                    }
                }""")
                self.assertEqual(
                    dom_export._expand_truncated_queries(many_expands_page, pace_ms=1), 1,
                )
                self.assertIn("last fully expanded", many_expands_page.inner_text("main"))
            finally:
                browser.close()

        captured = dom_export._public_turns(records)
        self.assertEqual(report["status"], "complete", report)
        self.assertEqual([turn["role"] for turn in captured], [turn["role"] for turn in turns])
        self.assertEqual(captured[0]["attachments"], ["image.jpg"])
        self.assertEqual(captured[0]["text"], turns[0]["text"])
        self.assertEqual(captured[1]["text"], turns[1]["markdown"])
        self.assertEqual(captured[3]["text"], same_markdown)
        self.assertEqual(captured[4]["text"], turns[4]["text"])
        self.assertEqual(captured[7]["text"], same_markdown)
        self.assertEqual(report["continuation_urls"], ["https://cgfixit.com/trade2.html"])
        self.assertEqual(short_report["status"], "complete", short_report)
        self.assertEqual(len(short_records), 2)
        self.assertEqual(html_report["status"], "partial", html_report)
        self.assertEqual(html_report["stop_reason"], "copy_failed")
        self.assertEqual(
            dom_export._public_turns(html_records)[1]["capture"], "dom_text_fallback",
        )
        self.assertEqual(truncated_report["status"], "partial", truncated_report)
        self.assertEqual(truncated_report["stop_reason"], "copy_failed")
        self.assertEqual(
            dom_export._public_turns(truncated_records)[1]["capture"], "dom_text_fallback",
        )

    def base_report(self):
        return DomCollectorTests().base_report()


class DomLiveTests(unittest.TestCase):
    """Optional live smoke; skipped unless DOM_EXPORT_LIVE=1 and Playwright works."""

    @classmethod
    def setUpClass(cls):
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
            f"https://www.perplexity.ai/search/{PART1_UUID}", pace_ms=750,
        )
        self.assertEqual(report["access"], "public")
        marker = "## Turn 1 (User)\n"
        self.assertIn(marker, md)
        first_user = md.split(marker, 1)[1].split("\n## Turn 2 (", 1)[0].lower()
        self.assertTrue(
            "liberation day" in first_user or "analyze this image" in first_user,
            msg="Part 1 opener missing from DOM markdown",
        )
        self.assertIn("image.jpg", first_user, msg="Part 1 opener attachment metadata missing")
        self.assertGreater(report["turns_seen"], 0)


class HostAllowTests(unittest.TestCase):
    def test_host_allowlist_rejects_suffix_lookalikes(self):
        from dom_export import _host_allowed
        self.assertTrue(_host_allowed("cgfixit.com"))
        self.assertTrue(_host_allowed("www.cgfixit.com"))
        self.assertTrue(_host_allowed("www.perplexity.ai"))
        self.assertFalse(_host_allowed("evilcgfixit.com"))
        self.assertFalse(_host_allowed("notperplexity.ai"))
        self.assertFalse(_host_allowed("example.com"))

    def test_continuation_url_is_canonical_and_rejects_unsafe_authority(self):
        safe = dom_export._safe_continuation_url
        self.assertEqual(
            safe("https://CGFIXIT.com.:443/x?q=1#f"),
            "https://cgfixit.com/x?q=1#f",
        )
        for url in (
            "https://user:pass@cgfixit.com/x",
            "https://cgfixit.com:444/x",
            "https://evilcgfixit.com/x",
            "http://cgfixit.com/x",
            "https://cgfixit.com:bad/x",
            "https://cgfixit.com/\x00x",
        ):
            with self.subTest(url=repr(url)):
                self.assertIsNone(safe(url))


if __name__ == "__main__":
    unittest.main()
