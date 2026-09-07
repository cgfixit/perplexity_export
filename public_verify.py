#!/usr/bin/env python3
"""Verify a public UUID thread anonymously through a temporary complete export."""
import argparse
import contextlib
import io
import re
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import pplx_export as exporter


def verify_saved_export(root, raw, uid):
    """Write, reopen, and offline-rebuild every export artifact in temporary storage."""
    run_id = "public-live-verification"
    report = {"format": exporter.FORMAT, "run_id": run_id, "started_at": exporter.now(),
              "mode": "public_live", "scope": "specified_threads",
              "account_completeness": "not_verified", "discovery": "explicit_public_uuid",
              "discovered": 1, "selected": 1, "exported": 0, "errors": [], "warnings": []}
    row = exporter.export_one(root, raw, uid)
    report["exported"] = 1
    report["warnings"].extend(f"{uid}: {warning}" for warning in row["warnings"])
    exporter.save_summary(root, report)
    if report["status"] != "completed_requested_scope" or report["warnings"] or report["errors"]:
        raise exporter.ExportError("Received content requires rendering review; no transcript was retained.")

    markdown_path = root / row["filename"]
    raw_path = root / "raw" / f"{uid}.json"
    record_path = root / "records" / f"{uid}.json"
    expected_files = {"INDEX.md", "export_report.json", row["filename"],
                      f"raw/{uid}.json", f"records/{uid}.json", f"runs/{run_id}/report.json"}
    actual_files = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if actual_files != expected_files:
        raise exporter.ExportError("Temporary export contained missing or unexpected files.")
    saved_raw = exporter.read_json(raw_path)
    saved_row = exporter.read_json(record_path)
    saved_report = exporter.read_json(root / "export_report.json")
    if saved_raw != raw or saved_row != row or saved_report != exporter.read_json(root / "runs" / run_id / "report.json"):
        raise exporter.ExportError("Temporary export did not round-trip exactly.")
    _, rebuilt_markdown, rebuilt_row = exporter.render(saved_raw, uid)
    markdown = markdown_path.read_text(encoding="utf-8")
    if rebuilt_markdown != markdown or rebuilt_row["turns"] != row["turns"]:
        raise exporter.ExportError("Saved raw data did not rebuild the same transcript.")

    rebuilt_root = root.parent / "offline-rebuild"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        offline_status = exporter.main(["--from-raw", str(root / "raw"), "--output", str(rebuilt_root)])
    rebuilt_files = list((rebuilt_root / "markdown").glob("*.md"))
    if offline_status != 0 or len(rebuilt_files) != 1 or rebuilt_files[0].read_bytes() != markdown_path.read_bytes():
        raise exporter.ExportError("The offline CLI rebuild did not reproduce the live Markdown export.")
    return markdown, row, exporter.transcript_digest(saved_raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread-url", required=True)
    parser.add_argument("--expected-turns", type=int)
    parser.add_argument("--expect-text")
    parser.add_argument("--expected-sha256", help="Expected SHA-256 of all canonicalized API entries.")
    parser.add_argument("--inspect", action="store_true", help="Report structural counts and digest; does not independently verify content.")
    args = parser.parse_args(argv)
    try:
        uid = str(UUID(exporter.parse_thread_url(args.thread_url)))
    except (exporter.ExportError, ValueError):
        parser.error("Use an HTTPS Perplexity thread URL containing its UUID. Anonymous slug resolution is unsupported.")
    if not args.inspect and (args.expected_turns is None or args.expected_turns < 1 or
                             not (args.expect_text or "").strip() or
                             not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256 or "")):
        parser.error("Provide a positive expected turn count, nonempty expected text, and lowercase SHA-256, or use --inspect.")
    if args.inspect and any(value is not None for value in (args.expected_turns, args.expect_text, args.expected_sha256)):
        parser.error("--inspect cannot be combined with content expectations.")
    try:
        client = exporter.Client(None)
        try:
            # Only a specific public UUID: never enumerate account history.
            with tempfile.TemporaryDirectory(prefix="pplx-public-check-") as directory:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    raw = exporter.fetch_detail(client, uid)
                    markdown, row, content_sha256 = verify_saved_export(Path(directory) / "live", raw, uid)
        finally:
            client.close()
        if args.inspect:
            print(f"RETRIEVED: {row['turns']} turns across {len(raw['pages'])} pages; content SHA-256 {content_sha256}. "
                  "Expected content has NOT been independently verified. Temporary export was deleted.")
            return 0
        if (row["turns"] != args.expected_turns or args.expect_text not in markdown or
                content_sha256 != args.expected_sha256):
            print("FAIL: exported turns, text, or complete-content digest do not match the supplied expectations.")
            return 1
        print("PASS: anonymous pagination, temporary export, saved-artifact validation, offline rebuild, and complete-content digest matched. "
              "Temporary transcript data was deleted.")
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
