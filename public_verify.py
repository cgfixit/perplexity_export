#!/usr/bin/env python3
"""Verify a public UUID thread through anonymous structured or native export."""
import argparse
import base64
import binascii
import contextlib
import hashlib
import io
import os
import re
import sys
import tempfile
from pathlib import Path
from uuid import UUID

import pplx_export as exporter


MAX_NATIVE_EXPORT_BYTES = 64 * 1024 * 1024


def fetch_native_markdown(client, uid):
    """Request and validate Perplexity's own complete Markdown export response."""
    payload = client.request("POST", exporter.api_path("export"),
                             {"thread_uuid": uid, "format": "md"})
    if not isinstance(payload, dict):
        raise exporter.ExportError("Native export response was not an object.")
    encoded = payload.get("file_content_64")
    filename = payload.get("filename")
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_NATIVE_EXPORT_BYTES * 2:
        raise exporter.ExportError("Native export response contained missing or oversized file data.")
    if (not isinstance(filename, str) or not filename.lower().endswith(".md") or
            any(character in filename for character in ("/", "\\", "\0", "\r", "\n"))):
        raise exporter.ExportError("Native export response contained an unsafe Markdown filename.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise exporter.ExportError("Native export response contained invalid base64 file data.") from None
    if not data or len(data) > MAX_NATIVE_EXPORT_BYTES:
        raise exporter.ExportError("Native Markdown export was empty or exceeded the safety limit.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise exporter.ExportError("Native Markdown export was not valid UTF-8.") from None
    if not text.strip() or "\0" in text:
        raise exporter.ExportError("Native Markdown export did not contain valid text.")
    return data


def atomic_bytes(path, data):
    """Atomically save exact native-export bytes without trusting its remote filename."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


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
    parser.add_argument("--expected-sha256",
                        help="Expected canonical-entry digest, or complete Markdown digest with --native-export.")
    parser.add_argument("--inspect", action="store_true", help="Report structural counts and digest; does not independently verify content.")
    parser.add_argument("--native-export", action="store_true",
                        help="Verify Perplexity's native complete Markdown export endpoint.")
    parser.add_argument("--output", type=Path,
                        help="With --native-export, atomically save the exact Markdown bytes to this path.")
    args = parser.parse_args(argv)
    try:
        uid = str(UUID(exporter.parse_thread_url(args.thread_url)))
    except (exporter.ExportError, ValueError):
        parser.error("Use an HTTPS Perplexity thread URL containing its UUID. Anonymous slug resolution is unsupported.")
    if args.inspect and args.native_export:
        parser.error("Choose either --inspect or --native-export.")
    if args.native_export:
        if args.expected_turns is not None or args.expect_text is not None:
            parser.error("--native-export accepts only the optional SHA-256 expectation.")
        if args.expected_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256):
            parser.error("--expected-sha256 must be 64 lowercase hexadecimal characters.")
    elif not args.inspect and (args.expected_turns is None or args.expected_turns < 1 or
                               not (args.expect_text or "").strip() or
                               not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256 or "")):
        parser.error("Provide a positive expected turn count, nonempty expected text, and lowercase SHA-256, or use --inspect.")
    if args.inspect and any(value is not None for value in (args.expected_turns, args.expect_text, args.expected_sha256)):
        parser.error("--inspect cannot be combined with content expectations.")
    if args.output is not None and not args.native_export:
        parser.error("--output requires --native-export.")
    try:
        client = exporter.Client(None)
        try:
            if args.native_export:
                markdown_bytes = fetch_native_markdown(client, uid)
                content_sha256 = hashlib.sha256(markdown_bytes).hexdigest()
                if args.expected_sha256 is not None and content_sha256 != args.expected_sha256:
                    print("FAIL: native Markdown export did not match the supplied complete-file SHA-256.")
                    return 1
                if args.output is not None:
                    atomic_bytes(args.output, markdown_bytes)
                    if args.output.resolve().read_bytes() != markdown_bytes:
                        raise exporter.ExportError("Saved native Markdown did not round-trip exactly.")
                saved = " Exact bytes were saved atomically and reopened." if args.output is not None else ""
                print(f"PASS: Perplexity returned a valid native Markdown export ({len(markdown_bytes)} bytes; "
                      f"SHA-256 {content_sha256}).{saved}")
                return 0
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
