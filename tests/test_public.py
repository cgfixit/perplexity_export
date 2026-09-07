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
    @staticmethod
    def content_sha256(pages):
        raw = {"format": e.FORMAT, "uuid": UID, "list_metadata": {},
               "pagination_complete": True, "pages": pages}
        return e.transcript_digest(raw)

    def invoke(self, responses, *extra):
        client = QueueClient(responses)
        output = io.StringIO()
        with (patch.object(e, "Client", return_value=client) as factory,
              patch.object(e, "export_one", wraps=e.export_one) as export_one,
              contextlib.redirect_stdout(output)):
            status = public_verify.main(["--thread-url", URL, *extra])
        factory.assert_called_once_with(None)
        self.assertTrue(client.closed)
        self.assertTrue(all(method == "GET" and UID in path for method, path, _ in client.calls))
        self.export_calls = export_one.call_count
        self.client_calls = len(client.calls)
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
        status, output = self.invoke(pages, "--expected-turns", "2", "--expect-text", "Synthetic private sentinel",
                                     "--expected-sha256", self.content_sha256(pages))
        self.assertEqual(status, 0)
        self.assertIn("PASS:", output)
        self.assertIn("temporary export", output)
        self.assertNotIn("Synthetic private sentinel", output)
        self.assertNotIn(UID, output)
        self.assertEqual(self.export_calls, 2)  # Live write plus the offline CLI rebuild.

    def test_wrong_turns_text_or_digest_is_failure(self):
        pages = [detail()]
        good_digest = self.content_sha256(pages)
        for count, text, expected_digest in (("2", "Question 1", good_digest),
                                             ("1", "missing phrase", good_digest),
                                             ("1", "Question 1", "0" * 64)):
            with self.subTest(count=count, text=text, expected_digest=expected_digest):
                status, output = self.invoke(pages, "--expected-turns", count, "--expect-text", text,
                                             "--expected-sha256", expected_digest)
                self.assertEqual(status, 1)
                self.assertNotIn("PASS:", output)

    def test_inspection_is_explicitly_not_independent_verification(self):
        status, output = self.invoke([detail()], "--inspect")
        self.assertEqual(status, 0)
        self.assertIn("NOT been independently verified", output)
        self.assertRegex(output, r"content SHA-256 [0-9a-f]{64}")
        self.assertIn("Temporary export was deleted", output)
        self.assertNotIn("PASS:", output)
        self.assertEqual(self.export_calls, 2)

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

    def test_renewed_signatures_for_the_same_known_asset_are_equivalent(self):
        s3 = "https://ppl-ai-file-upload.s3.amazonaws.com/same/object?AWSAccessKeyId=key&Expires=1&Signature={}"
        cloudfront = "https://d2z0o16i8xm8ak.cloudfront.net/same/chart?response-content-disposition=inline&Key-Pair-Id=key&Policy=p&Signature={}"
        s3_v4 = ("https://ppl-ai-file-upload.s3.amazonaws.com/same/modern?response-content-disposition=inline&"
                 "X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=credential&X-Amz-Date={}&X-Amz-Expires=60&"
                 "X-Amz-SignedHeaders=host&X-Amz-Signature={}")
        before = turn()
        before["attachments"] = [s3.format("old"), cloudfront.format("old"), s3_v4.format("old-date", "old")]
        after = turn()
        after["attachments"] = [s3.format("new"), cloudfront.format("new"), s3_v4.format("new-date", "new")]
        pages = [detail([before], True, "next"), detail([after, turn(2)])]
        self.assertEqual(e.transcript_digest({"format": e.FORMAT, "uuid": UID, "list_metadata": {},
                                              "pagination_complete": True, "pages": pages}),
                         e.digest(e.stable_entry_value([before, turn(2)])))
        status, output = self.invoke(pages, "--expected-turns", "2", "--expect-text", "Question 2",
                                     "--expected-sha256", self.content_sha256(pages))
        self.assertEqual(status, 0)
        self.assertIn("PASS:", output)

    def test_signature_equivalence_is_limited_to_the_same_known_asset(self):
        signed = "https://ppl-ai-file-upload.s3.amazonaws.com/{path}?AWSAccessKeyId=key&Expires=1&Signature=sig"
        cases = ((signed.format(path="a"), signed.format(path="b")),
                 (signed.format(path="a"), signed.format(path="a") + "&ordinary=changed"),
                 (signed.format(path="a") + "&ordinary=one", signed.format(path="a") + "&ordinary=two"),
                 (signed.format(path="a").replace("Signature=sig", "Signature="), signed.format(path="a")),
                 ("https://lookalike.example/a?AWSAccessKeyId=key&Expires=1&Signature=old",
                  "https://lookalike.example/a?AWSAccessKeyId=key&Expires=1&Signature=new"))
        for left, right in cases:
            with self.subTest(left=left, right=right):
                before, after = turn(), turn()
                before["attachments"], after["attachments"] = [left], [right]
                status, output = self.invoke([detail([before], True, "next"), detail([after, turn(2)])], "--inspect")
                self.assertEqual(status, 1)
                self.assertIn("changed across pages", output)

    def test_signature_renewal_cannot_hide_pagination_without_progress(self):
        signed = "https://ppl-ai-file-upload.s3.amazonaws.com/same/object?AWSAccessKeyId=key&Expires=1&Signature={}"
        before, after = turn(), turn()
        before["attachments"], after["attachments"] = [signed.format("old")], [signed.format("new")]
        status, output = self.invoke([detail([before], True, "next"), detail([after], True, "another")], "--inspect")
        self.assertEqual(status, 1)
        self.assertIn("pagination made no progress", output)
        self.assertEqual(self.client_calls, 2)

    def test_slug_and_unsafe_url_never_construct_client(self):
        for url in ("https://www.perplexity.ai/search/a-title-slug", "https://example.test/search/" + UID):
            with self.subTest(url=url), patch.object(e, "Client") as client, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    public_verify.main(["--thread-url", url, "--inspect"])
                self.assertEqual(error.exception.code, 2)
                client.assert_not_called()

    def test_missing_expectations_or_combined_inspection_rejected(self):
        for options in ([], ["--expected-turns", "0", "--expect-text", "x", "--expected-sha256", "0" * 64],
                        ["--expected-turns", "1", "--expect-text", "x", "--expected-sha256", "ABC"],
                        ["--inspect", "--expect-text", "x"]):
            with self.subTest(options=options), patch.object(e, "Client") as client, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    public_verify.main(["--thread-url", URL, *options])
                client.assert_not_called()
