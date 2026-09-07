#!/usr/bin/env python3
"""Verify a public UUID thread anonymously; never load account cookies or save content."""
import argparse
import contextlib
import io
import sys
from uuid import UUID

import pplx_export as exporter


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread-url", required=True)
    parser.add_argument("--expected-turns", type=int)
    parser.add_argument("--expect-text")
    parser.add_argument("--inspect", action="store_true", help="Report retrieval counts only; does not verify expected content.")
    args = parser.parse_args(argv)
    try:
        uid = str(UUID(exporter.parse_thread_url(args.thread_url)))
    except (exporter.ExportError, ValueError):
        parser.error("Use an HTTPS Perplexity thread URL containing its UUID. Anonymous slug resolution is unsupported.")
    if not args.inspect and (args.expected_turns is None or args.expected_turns < 1 or not (args.expect_text or "").strip()):
        parser.error("Provide a positive expected turn count and nonempty expected text, or use --inspect.")
    if args.inspect and (args.expected_turns is not None or args.expect_text is not None):
        parser.error("--inspect cannot be combined with content expectations.")
    try:
        client = exporter.Client(None)
        try:
            # Only a specific public UUID: never enumerate account history.
            with contextlib.redirect_stdout(io.StringIO()):
                raw = exporter.fetch_detail(client, uid)
                _, markdown, row = exporter.render(raw, uid)
        finally:
            client.close()
        if row["warnings"]:
            print("FAIL: received content requires rendering review; no transcript was saved.")
            return 1
        if args.inspect:
            print(f"RETRIEVED: {row['turns']} turns across {len(raw['pages'])} pages. Expected content has NOT been independently verified.")
            return 0
        if row["turns"] != args.expected_turns or args.expect_text not in markdown:
            print("FAIL: exported turns or text do not match the supplied expectations.")
            return 1
        print("PASS: anonymous retrieval completed and rendered content matched expected turns and text. No transcript was saved.")
        return 0
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except exporter.AuthError:
        print("FAIL: public access was denied or challenged; no account cookies were supplied.")
        return 1
    except exporter.ExportError as exc:
        print(f"FAIL: {exc}")
        return 1
    except Exception:
        # Never log response bodies, URLs, expected text, or transport exceptions.
        print("FAIL: anonymous retrieval, pagination, rendering, or cleanup failed. Public access may be unavailable.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
