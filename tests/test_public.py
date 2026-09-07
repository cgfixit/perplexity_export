"""Anonymous checks must verify content and never inherit account cookies."""
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import pplx_export as e
import public_verify
from test_export import QueueClient, UID, detail, turn

URL = f"https://www.perplexity.ai/search/{UID}"


class PublicTests(unittest.TestCase):
    def invoke(self, responses, *extra):
        client = QueueClient(responses)
        output = io.StringIO()
        with patch.object(e, "Client", return_value=client) as factory, contextlib.redirect_stdout(output):
            status = public_verify.main(["--thread-url", URL, *extra])
        factory.assert_called_once_with(None)
        self.assertTrue(client.closed)
        self.assertTrue(all(method == "GET" and UID in path for method, path, _ in client.calls))
        return status, output.getvalue()

    def test_anonymous_headers_have_no_cookie_or_csrf_token(self):
        response = Mock(status_code=200); response.json.return_value = {}
        session = Mock(); session.request.return_value = response
        client = e.Client(None, 0, session)
        client.request("GET", e.detail_path(UID, 0, True))
        headers = session.request.call_args.kwargs["headers"]
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("X-CSRFToken", headers)
        self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
        with self.assertRaises(e.ExportError):
            e.Client("", session=session)

    def test_public_check_verifies_all_pages_without_logging_content(self):
        pages = [detail([turn(1, "Synthetic private sentinel")], True, "next"), detail([turn(2)])]
        status, output = self.invoke(pages, "--expected-turns", "2", "--expect-text", "Synthetic private sentinel")
        self.assertEqual(status, 0)
        self.assertIn("PASS:", output)
        self.assertNotIn("Synthetic private sentinel", output)
        self.assertNotIn(UID, output)

    def test_wrong_turns_or_text_is_failure(self):
        for count, text in (("2", "Question 1"), ("1", "missing phrase")):
            with self.subTest(count=count, text=text):
                status, output = self.invoke([detail()], "--expected-turns", count, "--expect-text", text)
                self.assertEqual(status, 1)
                self.assertNotIn("PASS:", output)

    def test_inspection_is_explicitly_not_independent_verification(self):
        status, output = self.invoke([detail()], "--inspect")
        self.assertEqual(status, 0)
        self.assertIn("NOT been independently verified", output)
        self.assertNotIn("PASS:", output)

    def test_denial_and_transport_exceptions_are_not_reported_as_success(self):
        for error in (e.AuthError("fake private text"), RuntimeError("fake private text")):
            with self.subTest(error=type(error).__name__):
                status, output = self.invoke([error], "--inspect")
                self.assertEqual(status, 1)
                self.assertNotIn("fake private text", output)

    def test_changed_source_url_across_pages_is_not_silently_deduplicated(self):
        before = turn()
        before["blocks"].append({"web_result_block": {"web_results": [{"url": "https://example.test/a"}]}})
        after = turn()
        after["blocks"].append({"web_result_block": {"web_results": [{"url": "https://example.test/b"}]}})
        status, output = self.invoke([detail([before], True, "next"), detail([after, turn(2)])], "--inspect")
        self.assertEqual(status, 1)
        self.assertIn("changed across pages", output)

    def test_slug_and_unsafe_url_never_construct_client(self):
        for url in ("https://www.perplexity.ai/search/a-title-slug", "https://example.test/search/" + UID):
            with self.subTest(url=url), patch.object(e, "Client") as client, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    public_verify.main(["--thread-url", url, "--inspect"])
                self.assertEqual(error.exception.code, 2)
                client.assert_not_called()

    def test_missing_expectations_or_combined_inspection_rejected(self):
        for options in ([], ["--expected-turns", "0", "--expect-text", "x"], ["--inspect", "--expect-text", "x"]):
            with self.subTest(options=options), patch.object(e, "Client") as client, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    public_verify.main(["--thread-url", URL, *options])
                client.assert_not_called()
