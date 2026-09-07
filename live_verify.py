#!/usr/bin/env python3
"""Explicit, local-only signed-in smoke test. Never run with account cookies in CI."""
import argparse
import contextlib
import io
import os
import tempfile
from pathlib import Path

import pplx_export as exporter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--thread-id")
    selector.add_argument("--thread-url")
    parser.add_argument("--cookies", default=str(exporter.SCRIPT_DIR / "pplx_cookies.txt"))
    parser.add_argument("--expected-turns", type=int, required=True, help="Exact turn count you independently checked in the browser.")
    parser.add_argument("--expect-text", required=True, help="A distinctive text fragment you independently checked in the browser.")
    args = parser.parse_args(argv)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        parser.error("Live account verification is local-only. Do not add session cookies to GitHub Actions.")
    if args.expected_turns < 1 or not args.expect_text.strip():
        parser.error("Provide a positive turn count and nonempty expected text.")
    with tempfile.TemporaryDirectory(prefix="pplx-live-check-") as directory:
        root = Path(directory)
        command = ["--cookies", args.cookies, "--output", directory,
                   "--thread-id" if args.thread_id else "--thread-url", args.thread_id or args.thread_url]
        # Do not repeat the thread URL, title, identifier, or response in logs.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = exporter.main(command)
        report_path = root / "export_report.json"
        if result != 0 or not report_path.is_file():
            print("FAIL: retrieval or rendering did not finish cleanly. Run pplx_export.py locally to inspect its report; do not share cookies or raw content.")
            return 1
        report = exporter.read_json(report_path)
        files = list((root / "markdown").glob("*.md"))
        records = list((root / "records").glob("*.json"))
        if report["exported"] != 1 or len(files) != 1 or len(records) != 1:
            print("FAIL: expected exactly one exported conversation.")
            return 1
        row = exporter.read_json(records[0])
        if row["turns"] != args.expected_turns or args.expect_text not in files[0].read_text(encoding="utf-8"):
            print("FAIL: transcript did not match the browser-verified turn count and text.")
            return 1
        print("PASS: one conversation exported and matched the supplied browser-verified turn count and text. This does not prove account-wide coverage.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
