"""Regression tests. Run: python -m unittest -v

All HTTP responses are simulated. These tests do not authenticate to Perplexity.
"""
import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pplx_export as e


UID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
UID2 = "aaaaaaaa-bbbb-cccc-dddd-ffffffffffff"


def turn(n=1, answer=None):
    return {"uuid": f"entry-{n}", "query_str": f"Question {n}",
            "blocks": [{"intended_usage": "ask_text", "markdown_block": {"answer": answer or f"Answer {n}"}}]}


def detail(entries=None, more=False, cursor=None, uid=UID):
    result = {"thread_metadata": {"uuid": uid, "title": "A conversation", "created_at": "2026-09-07T10:00:00Z"},
              "entries": [turn()] if entries is None else entries, "has_next_page": more}
    if cursor is not None:
        result["next_cursor"] = cursor
    return result


def snapshot(entries=None, uid=UID):
    return {"format": e.FORMAT, "uuid": uid, "pagination_complete": True, "list_metadata": {},
            "pages": [detail(entries, uid=uid)]}


class QueueClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if not self.responses:
            raise AssertionError("Unexpected request")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)

    def close(self):
        self.closed = True


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.quiet = contextlib.redirect_stdout(io.StringIO())
        self.quiet.__enter__()

    def tearDown(self):
        self.quiet.__exit__(None, None, None)
        self.temp.cleanup()

    def run_live(self, responses, extra=()):
        cookie = self.root / "cookies.txt"
        cookie.write_text("session=FAKE-TEST-ONLY")
        client = QueueClient(responses)
        with patch.object(e, "Client", return_value=client):
            status = e.main(["-o", str(self.root / "output"), "-c", str(cookie), *extra])
        return status, client

    def test_index_short_pages_do_not_truncate(self):
        rows = [{"uuid": f"thread-{i}"} for i in range(125)]
        client = QueueClient([rows[i:i+7] for i in range(0, 125, 7)] + [[]])
        self.assertEqual(len(e.fetch_index(client)), 125)
        self.assertEqual(client.calls[-1][2]["offset"], 125)

    def test_index_recognized_wrapper_and_total(self):
        client = QueueClient([{"threads": [{"uuid": UID}], "has_next_page": False, "total_count": 1, "total": 1}])
        self.assertEqual(e.fetch_index(client)[0]["uuid"], UID)

    def test_bad_listing_schemas_fail(self):
        for value in ({"unexpected": []}, {"threads": [], "items": []}, "html", ["bad"], [{"uuid": "../../bad"}]):
            with self.subTest(value=value), self.assertRaises(e.ExportError):
                e.fetch_index(QueueClient([value]))

    def test_repeated_listing_fails(self):
        page = [{"uuid": UID}]
        with self.assertRaisesRegex(e.ExportError, "Repeated"):
            e.fetch_index(QueueClient([page, page]))

    def test_inconsistent_advertised_listing_total_fails(self):
        for total in (0, 2):
            with self.subTest(total=total), self.assertRaises(e.ExportError):
                e.fetch_index(QueueClient([{"threads": [{"uuid": UID}], "has_next_page": False, "total": total}]))

    def test_invalid_or_conflicting_advertised_listing_total_fails(self):
        for totals in ({"total": "1"}, {"total": False}, {"total": -1}, {"total_count": 1, "total": 0}):
            with self.subTest(totals=totals), self.assertRaises(e.ExportError):
                e.fetch_index(QueueClient([{**totals, "threads": [{"uuid": UID}], "has_next_page": False}]))

    def test_empty_last_page_cannot_hide_advertised_total(self):
        with self.assertRaises(e.ExportError):
            e.fetch_index(QueueClient([[{"uuid": UID}], {"threads": [], "total": 2, "has_next_page": False}]))

    def test_detail_follows_opaque_cursor_and_keeps_125_turns(self):
        pages = [detail([turn(n) for n in range(1, 51)], True, "cursor-A"),
                 detail([turn(n) for n in range(51, 101)], True, "cursor-B"),
                 detail([turn(n) for n in range(101, 126)])]
        pages[1]["later_only_metadata"] = {"must": "survive"}
        client = QueueClient(pages)
        raw = e.fetch_detail(client, UID)
        self.assertEqual(raw["pages"], pages)
        for i, cursor in enumerate(("0", "cursor-A", "cursor-B")):
            query = parse_qs(urlsplit(client.calls[i][1]).query)
            self.assertEqual(query["offset"], [cursor])
            self.assertEqual(query["from_first"], ["true" if i == 0 else "false"])
        _, md, row = e.render(raw)
        self.assertEqual(row["turns"], 125)
        self.assertLess(md.index("Question 1\n"), md.index("Question 125\n"))

    def test_missing_cursor_fails_and_checkpoints(self):
        seen = []
        with self.assertRaisesRegex(e.ExportError, "cursor"):
            e.fetch_detail(QueueClient([detail(more=True)]), UID, checkpoint=lambda *x: seen.append(x))
        self.assertEqual(len(seen), 1)

    def test_repeated_page_and_cursor_fail(self):
        cases = [[detail(more=True, cursor="a"), detail(more=True, cursor="b")],
                 [detail(more=True, cursor="a"), detail([turn(2)], True, "a")]]
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaises(e.ExportError):
                e.fetch_detail(QueueClient(pages), UID)

    def test_invalid_detail_never_becomes_partial_success(self):
        for page in ([], {}, {"entries": []}, {"entries": [], "has_next_page": "false"},
                     {"entries": [None], "has_next_page": False}, detail([], True, "a")):
            with self.subTest(page=page), self.assertRaises(e.ExportError):
                e.fetch_detail(QueueClient([page]), UID)

    def test_boundary_overlap_deduplicates_by_entry_identity(self):
        raw = e.fetch_detail(QueueClient([detail([turn(1), turn(2)], True, "a"), detail([turn(2), turn(3)])]), UID)
        self.assertEqual(e.render(raw)[2]["turns"], 3)
        self.assertEqual(len(raw["pages"][1]["entries"]), 2)

    def test_changed_entry_across_pages_is_not_silently_overwritten(self):
        with self.assertRaisesRegex(e.ExportError, "changed"):
            e.fetch_detail(QueueClient([detail([turn(1)], True, "a"), detail([turn(1, "changed"), turn(2)])]), UID)

    def test_identical_turn_text_without_ids_is_retained(self):
        item = turn(); item.pop("uuid")
        self.assertEqual(e.render(snapshot([item, item]))[2]["turns"], 2)

    def test_all_answer_blocks_unicode_code_sources_and_attachments(self):
        item = turn()
        item["query_str"] = "  First line\n第二行\n"
        item["blocks"].extend([
            {"markdown_block": {"answer": "Second answer block"}},
            {"code_block": {"language": "python", "code": "print('```')"}},
            {"web_result_block": {"web_results": [{"name": "A [source]", "url": "https://example.com/a(b)", "snippet": "Original snippet"}]}},
            {"plan_block": {"goals": [{"description": "Plan survives"}]}},
        ])
        item["attachments"] = [{"filename": "file.pdf", "url": "https://files.example/file.pdf"}]
        _, md, row = e.render(snapshot([item]))
        for text in ("  First line\n第二行\n", "Answer 1", "Second answer block", "print('```')", "Original snippet", "Plan survives", "file.pdf"):
            self.assertIn(text, md)
        self.assertFalse(md.startswith("---"))
        self.assertEqual(row["warnings"], [])

    def test_unknown_blocks_are_preserved_and_flagged(self):
        item = turn(); item["blocks"].append({"future_block": {"text": "NEW CONTENT"}})
        _, md, row = e.render(snapshot([item]))
        self.assertIn("NEW CONTENT", md)
        self.assertTrue(row["warnings"])

    def test_legacy_answer_sources_are_not_dropped(self):
        item = {"query_str": "Legacy", "text": json.dumps({"answer": "Preserved answer", "web_results": [{"url": "https://example.org/source"}]})}
        _, md, _ = e.render(snapshot([item]))
        self.assertIn("Preserved answer", md)
        self.assertIn("https://example.org/source", md)

    def test_cli_round_trip_preserves_identity_and_turns(self):
        raw = {"meta": {"context_uuid": UID, "title": "CLI chat"}, "turns": [{"query": "CLI prompt", "answer": "CLI answer"}]}
        source = self.root / "source"; source.mkdir()
        (source / f"{UID}.json").write_text(json.dumps(raw))
        with patch.object(e, "Client", side_effect=AssertionError("Offline must not authenticate")):
            status = e.main(["--from-raw", str(source), "-o", str(self.root / "out")])
        self.assertEqual(status, 1)  # Explicit legacy-completeness review note.
        md = (self.root / "out" / "markdown" / f"chat_{UID}.md").read_text()
        self.assertIn("CLI prompt", md); self.assertIn("CLI answer", md)
        rebuilt = e.render(e.read_json(self.root / "out" / "raw" / f"{UID}.json"), UID)
        self.assertEqual(rebuilt[2]["turns"], 1)
        self.assertEqual(rebuilt[2]["title"], "CLI chat")

    def test_legacy_missing_payload_ids_recovered_from_filenames(self):
        source = self.root / "raw"; source.mkdir()
        for uid in (UID, UID2):
            (source / f"{uid}.json").write_text(json.dumps({"entries": [turn()], "thread_metadata": {"title": "Same title"}}))
        e.main(["--from-raw", str(source), "-o", str(self.root / "out")])
        self.assertEqual(len(list((self.root / "out" / "markdown").glob("*.md"))), 2)

    def test_legacy_list_metadata_survives_future_rebuilds(self):
        old = {"entries": [turn()]}
        e.export_one(self.root, old, UID, {"uuid": UID, "title": "Recovered title"})
        archived = e.read_json(self.root / "raw" / f"{UID}.json")
        self.assertEqual(archived["original"], old)
        self.assertEqual(e.render(archived, UID)[2]["title"], "Recovered title")

    def test_unknown_raw_and_identity_conflicts_fail(self):
        for raw, fallback in (({}, UID), (detail(more=True), UID), (snapshot(), UID2), (snapshot(uid="unknown"), None)):
            with self.subTest(raw=raw), self.assertRaises(e.ExportError):
                e.render(raw, fallback)

    def test_atomic_failure_retains_previous_file(self):
        path = self.root / "a.md"; path.write_text("old")
        with patch.object(e.os, "replace", side_effect=OSError("disk")), self.assertRaises(OSError):
            e.atomic_text(path, "new")
        self.assertEqual(path.read_text(), "old")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_unsafe_output_and_ids_rejected(self):
        with self.assertRaises(e.ExportError): e.inside(self.root, "../outside")
        for uid in ("../outside", "unknown", "a/b", "", None, "a?x=1"):
            with self.subTest(uid=uid), self.assertRaises(e.ExportError): e.valid_id(uid)

    def test_failed_raw_conversion_returns_nonzero_process_exit(self):
        source = self.root / "source"; source.mkdir()
        (source / f"{UID}.json").write_text("{")
        result = subprocess.run([sys.executable, e.__file__, "--from-raw", str(source), "-o", str(self.root / "out")], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        report = e.read_json(self.root / "out" / "export_report.json")
        self.assertEqual(report["exported"], 0)
        self.assertEqual(report["status"], "needs_review")

    def test_main_live_rerun_refreshes_and_repairs_markdown(self):
        status, client = self.run_live([[{"uuid": UID}], [], [], detail()])
        self.assertEqual(status, 0); self.assertTrue(client.closed)
        output = self.root / "output"
        (output / "markdown" / f"chat_{UID}.md").unlink()
        updated = detail([turn(), turn(2)])
        updated["thread_metadata"]["title"] = "Renamed"
        status, _ = self.run_live([[{"uuid": UID}], [], [], updated])
        self.assertEqual(status, 0)
        files = list((output / "markdown").glob("*.md"))
        self.assertEqual(len(files), 1)
        self.assertIn("Question 2", files[0].read_text())
        self.assertIn("Renamed", files[0].read_text())

    def test_recent_discovery_adds_missing_thread(self):
        status, _ = self.run_live([[{"uuid": UID}], [], [{"uuid": UID2}], detail(), detail(uid=UID2)])
        self.assertEqual(status, 0)
        self.assertEqual(len(list((self.root / "output" / "markdown").glob("*.md"))), 2)

    def test_failed_fetch_retains_prior_raw_and_received_pages(self):
        self.run_live([[{"uuid": UID}], [], [], detail()])
        old = (self.root / "output" / "raw" / f"{UID}.json").read_bytes()
        status, _ = self.run_live([[{"uuid": UID}], [], [], detail(more=True)])
        self.assertEqual(status, 1)
        self.assertEqual((self.root / "output" / "raw" / f"{UID}.json").read_bytes(), old)
        self.assertTrue(list((self.root / "output" / "runs").glob(f"*/received/{UID}/*.json")))

    def test_auth_failure_stops_and_closes(self):
        status, client = self.run_live([e.AuthError("Session expired")])
        self.assertEqual(status, 1); self.assertTrue(client.closed)

    def test_requested_scope_is_not_reported_as_account_complete(self):
        status, _ = self.run_live([detail()], ["--thread-id", UID])
        self.assertEqual(status, 0)
        report = e.read_json(self.root / "output" / "export_report.json")
        self.assertEqual(report["scope"], "specified_threads")
        self.assertEqual(report["account_completeness"], "not_verified")

    def test_live_failure_preserves_other_completed_threads(self):
        status, _ = self.run_live([[{"uuid": UID}, {"uuid": UID2}], [], [], detail(), []])
        self.assertEqual(status, 1)
        output = self.root / "output"
        self.assertTrue((output / "markdown" / f"chat_{UID}.md").exists())
        self.assertEqual(e.read_json(output / "export_report.json")["exported"], 1)

    def test_second_process_cannot_write_into_active_export(self):
        output = self.root / "output"; output.mkdir(); (output / ".export.lock").mkdir()
        status, client = self.run_live([])
        self.assertEqual(status, 1); self.assertEqual(client.calls, [])


class HTTPTests(unittest.TestCase):
    def test_cookie_stays_on_fixed_origin_and_redirects_disabled(self):
        response = Mock(status_code=200, headers={}); response.json.return_value = []
        session = Mock(); session.request.return_value = response
        client = e.Client("__Secure-next-auth.session-token.0=FAKE; another=VALUE", 0, session)
        client.request("POST", e.api_path("list_ask_threads"), {"limit": 20})
        args, kwargs = session.request.call_args
        self.assertTrue(args[1].startswith(e.BASE + "/rest/thread/"))
        self.assertFalse(kwargs["allow_redirects"]); self.assertTrue(kwargs["verify"])
        self.assertNotIn("cookies", kwargs)
        self.assertNotIn("User-Agent", kwargs["headers"])
        for path in ("https://evil.example/a", "//evil.example/a", "/rest/thread/\\evil"):
            with self.assertRaises(e.ExportError): client.request("GET", path)
        self.assertEqual(session.request.call_count, 1)

    def test_redirect_is_not_followed(self):
        session = Mock(); session.request.return_value = Mock(status_code=302)
        with self.assertRaisesRegex(e.ExportError, "redirected"):
            e.Client("session=FAKE", 0, session).request("GET", e.detail_path(UID, 0, True))
        self.assertEqual(session.request.call_count, 1)

    def test_cookie_newline_injection_rejected(self):
        with self.assertRaises(e.ExportError): e.Client("a=b\nInjected: yes", session=Mock())

    def test_network_exceptions_cannot_print_cookie(self):
        session = Mock(); session.request.side_effect = RuntimeError("secret-cookie-value")
        with patch.object(e.time, "sleep"), self.assertRaises(e.ExportError) as error:
            e.Client("session=secret-cookie-value", 0, session).request("GET", e.detail_path(UID, 0, True))
        self.assertNotIn("secret-cookie-value", str(error.exception))
        self.assertEqual(session.request.call_count, 5)

    def test_retry_after_honored(self):
        success = Mock(status_code=200, headers={}); success.json.return_value = {}
        session = Mock(); session.request.side_effect = [Mock(status_code=429, headers={"Retry-After": "12"}), success]
        with patch.object(e.time, "sleep") as sleep:
            e.Client("a=b", 0, session).request("GET", e.detail_path(UID, 0, True))
        sleep.assert_any_call(12.0)

    def test_permanent_http_error_not_retried(self):
        session = Mock(); session.request.return_value = Mock(status_code=404)
        with self.assertRaises(e.ExportError):
            e.Client("a=b", 0, session).request("GET", e.detail_path(UID, 0, True))
        self.assertEqual(session.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
