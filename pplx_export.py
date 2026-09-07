#!/usr/bin/env python3
"""Export accessible Perplexity conversations to local, ordinary Markdown files.

Python 3.10+. Live export: pip install -r requirements.txt
    python pplx_export.py --limit 5
    python pplx_export.py -o ./my-perplexity-history
Offline rebuild (no third-party dependencies or cookies):
    python pplx_export.py --from-raw ./my-perplexity-history/raw -o ./rebuilt

Cookies default to pplx_cookies.txt beside this script. Output defaults to
pplx_export/ beside this script. Live runs always refresh listing and details.
The private web API is not a supported account-export API. A completed crawl
does not prove that Perplexity exposed every account content type. See README.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import UUID, uuid4

BASE = "https://www.perplexity.ai"
API_VERSION = "2.18"
FORMAT = "pplx-markdown-export/1"
IMPORT_FORMAT = "pplx-markdown-import/1"
SCRIPT_DIR = Path(__file__).resolve().parent
ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
MAX_PAGES = 10000


class ExportError(Exception):
    """A safe-to-display, actionable export error (never includes cookies)."""


class AuthError(ExportError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def valid_id(value):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ExportError("Missing or unsafe thread identifier; raw input retained.")
    if value.lower() in {"unknown", "untitled", "none", "null"}:
        raise ExportError("Unresolved thread identifier; refusing to overwrite another chat.")
    return value


def identifier(meta):
    if not isinstance(meta, dict):
        raise ExportError("Thread metadata must be an object.")
    return valid_id(meta.get("uuid") or meta.get("context_uuid") or meta.get("session_id"))


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ExportError("Output path escapes the export directory.")
    return path


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def release_lock(lock):
    """Remove only an empty lock directory; preserve unexpected contents."""
    try:
        lock.rmdir()
    except OSError:
        return False
    return True


def close_client(client, report):
    """Do not discard completed exports because a client cleanup hook failed."""
    try:
        client.close()
    except Exception:
        report["warnings"].append("HTTP client cleanup failed; inspect the export report before retrying.")


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        raise ExportError("Cannot read valid UTF-8 JSON; check the input file.") from None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def api_path(name, params=None):
    query = [("version", API_VERSION), ("source", "default"), *(params or [])]
    return "/rest/thread/" + name + "?" + urlencode(query)


def parse_thread_url(value):
    """Return a validated URL path selector; never request the user-supplied URL."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        raise ExportError("Invalid Perplexity thread URL.") from None
    if (parsed.scheme != "https" or parsed.netloc not in {"perplexity.ai", "www.perplexity.ai"}
            or any(c in value for c in ("\\", "\n", "\r", "\t"))):
        raise ExportError("Thread URL must use HTTPS on perplexity.ai or www.perplexity.ai, without credentials or a port.")
    match = re.fullmatch(r"/(?:search|computer/tasks)/([A-Za-z0-9][A-Za-z0-9._-]{0,255})/?", parsed.path)
    if not match:
        raise ExportError("Unsupported thread URL path. Use /search/<slug-or-uuid> or /computer/tasks/<uuid>.")
    return match.group(1)


def select_threads(client, ids, urls, checkpoint):
    selected = {valid_id(uid): {"uuid": uid} for uid in ids}
    slugs = []
    for url in urls:
        selector = parse_thread_url(url)
        try:
            uid = str(UUID(selector))
        except ValueError:
            slugs.append(selector)
        else:
            selected[uid] = {"uuid": uid}
    if slugs:
        rows = fetch_index(client, checkpoint)
        recent = client.request("GET", api_path("list_recent", [("exclude_asi", "false")]))
        checkpoint("recent", 0, recent)
        extras, _ = list_items(recent)
        rows.extend(extras)
        for slug in slugs:
            matches = {}
            for row in rows:
                candidate = row.get("slug")
                if candidate != slug and isinstance(row.get("url"), str):
                    candidate_url = row["url"]
                    if candidate_url.startswith("/"):
                        candidate_url = BASE + candidate_url
                    try:
                        candidate = parse_thread_url(candidate_url)
                    except ExportError:
                        continue
                if candidate == slug:
                    matches[identifier(row)] = row
            if len(matches) != 1:
                raise ExportError("Shared-link slug could not be resolved uniquely in your account history. Use --thread-id with the actual UUID from the browser's /rest/thread/ request; no slug-to-ID guessing was attempted.")
            selected.update(matches)
    return list(selected.values())


def detail_path(uid, cursor, first):
    return api_path(valid_id(uid), [
        ("with_schematized_response", "true"), ("with_parent_info", "true"),
        ("limit", 50), ("offset", cursor), ("from_first", str(first).lower()),
        ("with_first_entry", "false"), ("with_latest_entry", "false"),
        ("supported_block_use_cases", "answer_modes"),
        ("supported_block_use_cases", "search_result_widgets"),
        ("supported_block_use_cases", "preserve_latex"),
    ])


def retry_wait(header, attempt):
    """Respect Retry-After; refuse impractically long waits rather than retry early."""
    if header:
        try:
            seconds = float(header)
        except (TypeError, ValueError):
            try:
                date = parsedate_to_datetime(header)
                seconds = (date - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = 5 * 2**attempt
        if not math.isfinite(seconds):
            seconds = 5 * 2**attempt
        if seconds > 300:
            raise ExportError("Server requested a long retry delay. Try this export later.")
        return max(0, seconds)
    return min(60, 5 * 2**attempt)


class Client:
    def __init__(self, cookie_header, delay=1.0, session=None):
        # None explicitly selects anonymous public-thread verification.
        value = "" if cookie_header is None else cookie_header.strip().lstrip("\ufeff")
        if value.lower().startswith("cookie:"):
            value = value.split(":", 1)[1].strip()
        if cookie_header is not None and (not value or "=" not in value or "\r" in value or "\n" in value):
            raise ExportError("Cookie file must contain one Cookie header value on one line.")
        if session is None:
            try:
                from curl_cffi import requests
            except ImportError:
                raise ExportError("Install the live-export dependency: python -m pip install -r requirements.txt") from None
            session = requests.Session(impersonate="chrome")
        self.session = session
        self.delay = delay
        self.last_request = None
        # Send this explicit header ONLY to our fixed HTTPS API origin. Never
        # attach it to a generic cookie jar or follow redirects with it.
        self.headers = {
            "Accept": "application/json",
            "Origin": BASE, "Referer": BASE + "/library",
            "x-app-apiclient": "default", "x-app-apiversion": API_VERSION,
        }
        if cookie_header is not None:
            self.headers["Cookie"] = value
        for part in value.split(";"):
            key, _, token = part.strip().partition("=")
            if key == "csrftoken":
                self.headers["X-CSRFToken"] = token

    def close(self):
        self.session.close()

    def request(self, method, path, body=None):
        if method not in {"GET", "POST"} or not path.startswith("/rest/thread/"):
            raise ExportError("Refusing a request outside the read-only thread API.")
        parsed = urlsplit(BASE + path)
        if parsed.scheme != "https" or parsed.netloc != "www.perplexity.ai" or "\\" in path:
            raise ExportError("Refusing to send cookies to an unapproved destination.")
        for attempt in range(5):
            if self.last_request is not None:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            kwargs = dict(headers=self.headers, timeout=60, allow_redirects=False, verify=True)
            if body is not None:
                kwargs["json"] = body
            try:
                r = self.session.request(method, BASE + path, **kwargs)
            except Exception:
                # Library exceptions can contain headers/URLs. Do not print them.
                if attempt == 4:
                    raise ExportError("Network request failed after five attempts.") from None
                time.sleep(retry_wait(None, attempt))
                continue
            if r.status_code in {401, 403}:
                raise AuthError("Login expired or request blocked (401/403). Recopy your own browser cookies and retry.")
            if 300 <= r.status_code < 400:
                raise ExportError("API redirected the request. Stopped without forwarding your cookies.")
            if r.status_code in {408, 429, 500, 502, 503, 504, 520, 522, 524}:
                if attempt == 4:
                    raise ExportError(f"HTTP {r.status_code} persisted after five attempts.")
                wait = retry_wait(r.headers.get("Retry-After"), attempt)
                print(f"  HTTP {r.status_code}; retrying in {wait:.0f}s.")
                time.sleep(wait)
                continue
            if r.status_code != 200:
                raise ExportError(f"API returned HTTP {r.status_code}; this endpoint may have changed.")
            try:
                return r.json()
            except (ValueError, TypeError):
                raise ExportError("API returned non-JSON content; login challenge or endpoint change.") from None
        raise ExportError("Request did not complete.")


def list_items(payload):
    if isinstance(payload, list):
        rows, envelope = payload, {}
    elif isinstance(payload, dict):
        keys = [k for k in ("threads", "items", "data") if isinstance(payload.get(k), list)]
        if len(keys) != 1:
            raise ExportError("Unrecognized or ambiguous thread-list response; refusing an empty-success export.")
        rows, envelope = payload[keys[0]], payload
    else:
        raise ExportError("Thread-list response must be a list or recognized list wrapper.")
    for row in rows:
        identifier(row)
    if "has_next_page" in envelope and type(envelope["has_next_page"]) is not bool:
        raise ExportError("Invalid thread-list pagination flag.")
    return rows, envelope


def fetch_index(client, checkpoint=lambda *args: None):
    found, pages, tokens = {}, set(), set()
    offset = 0
    for page_no in range(MAX_PAGES):
        body = dict(limit=20, ascending=False, offset=offset, search_term="", exclude_asi=False, include_assets=True)
        payload = client.request("POST", api_path("list_ask_threads"), body)
        checkpoint("listing", page_no, payload)
        rows, wrapper = list_items(payload)
        more = wrapper.get("has_next_page")
        total = wrapper.get("total_count", wrapper.get("total"))
        if not rows:
            if more is True:
                raise ExportError("Thread-list pagination stopped making progress before its end.")
            if isinstance(total, int) and not isinstance(total, bool) and len(found) < total:
                raise ExportError("Thread-list ended before its advertised total.")
            break
        page_key = digest([identifier(r) for r in rows])
        new = {identifier(r): r for r in rows if identifier(r) not in found}
        if page_key in pages or not new:
            raise ExportError("Repeated thread-list page; export completeness is unverified. Retry while account is idle.")
        pages.add(page_key)
        found.update(new)
        print(f"  Discovered {len(found)} conversations.")
        total = wrapper.get("total_count", wrapper.get("total"))
        if more is False:
            if isinstance(total, int) and not isinstance(total, bool) and len(found) < total:
                raise ExportError("Thread-list ended before its advertised total.")
            break
        next_cursor = wrapper.get("next_cursor")
        if next_cursor is not None and next_cursor != "":
            if type(next_cursor) not in (str, int) or str(next_cursor) in tokens:
                raise ExportError("Invalid or repeated listing cursor.")
            tokens.add(str(next_cursor))
            offset = next_cursor
        elif isinstance(offset, int):
            # For the observed offset-based listing, a short page isn't proof
            # of exhaustion: request the next page until explicit end/empty.
            offset += len(rows)
        else:
            raise ExportError("Listing cursor disappeared before the end.")
    else:
        raise ExportError("Thread-list page safety limit reached; export is incomplete.")
    return list(found.values())


def validate_entries(page):
    if not isinstance(page, dict) or not isinstance(page.get("entries"), list):
        raise ExportError("Unrecognized thread detail schema; expected an entries array.")
    if any(not isinstance(e, dict) for e in page["entries"]):
        raise ExportError("Thread contains malformed entries.")
    return page["entries"]


def join_entries(pages):
    out, identified = [], {}
    for page in pages:
        for entry in validate_entries(page):
            uid = entry.get("entry_uuid") or entry.get("uuid") or entry.get("id")
            if uid is not None:
                key = str(uid)
                if key in identified:
                    if identified[key] != entry:
                        raise ExportError("An entry changed across pages. Retry while the conversation is idle.")
                    continue
                identified[key] = entry
            # Do not deduplicate identical text without an identity: two real
            # turns may contain exactly the same question and answer.
            out.append(entry)
    return out


def fetch_detail(client, uid, meta=None, checkpoint=lambda *args: None):
    uid = valid_id(uid)
    pages, seen_pages, cursors = [], set(), {"0"}
    cursor, first, prior_count = 0, True, 0
    for n in range(MAX_PAGES):
        page = client.request("GET", detail_path(uid, cursor, first))
        checkpoint(uid, n, page)
        entries = validate_entries(page)
        if type(page.get("has_next_page")) is not bool:
            raise ExportError("Missing/invalid has_next_page flag; cannot establish thread completion.")
        fingerprint = digest(entries)
        if entries and fingerprint in seen_pages:
            raise ExportError("Repeated detail page; stopped instead of duplicating messages forever.")
        seen_pages.add(fingerprint)
        pages.append(page)
        count = len(join_entries(pages))
        if entries and count == prior_count:
            raise ExportError("Detail pagination made no progress.")
        prior_count = count
        if not page["has_next_page"]:
            return {"format": FORMAT, "uuid": uid, "list_metadata": meta or {},
                    "fetched_at": now(), "pagination_complete": True, "pages": pages}
        if not entries:
            raise ExportError("Empty detail page still claims more data.")
        cursor = page.get("next_cursor")
        if type(cursor) not in (str, int) or str(cursor) == "" or str(cursor) in cursors:
            raise ExportError("Missing or repeated next_cursor. Saved received pages; cannot safely infer the next page.")
        cursors.add(str(cursor))
        first = False
    raise ExportError("Detail page safety limit reached; conversation is incomplete.")


def fence(value, language="json"):
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)
    size = max([3, *(len(m.group()) + 1 for m in re.finditer(r"`+", text))])
    ticks = "`" * size
    language = re.sub(r"[^A-Za-z0-9_+-]", "", language)
    return f"{ticks}{language}\n{text}\n{ticks}"


def text_value(value):
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return fence(value)


def first_text(mapping, keys):
    for key in keys:
        if mapping.get(key) is not None and mapping[key] != "":
            return text_value(mapping[key])
    return ""


def md_label(value):
    return str(value).replace("\n", " ").replace("\r", " ").replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")


def render_entry(entry, number, warnings):
    query = first_text(entry, ("query_str", "query", "prompt"))
    lines = [f"## {number}. You", "", query or "*(No prompt returned.)*", ""]
    if not query:
        warnings.append(f"Turn {number}: no recognized prompt text.")
    lines.extend([f"## {number}. Perplexity", ""])
    info = [str(entry[k]) for k in ("display_model", "created_at") if entry.get(k)]
    if info:
        lines.extend([" · ".join(info), ""])
    blocks = entry.get("blocks", [])
    if blocks is None:
        blocks = []
    if not isinstance(blocks, list):
        raise ExportError("Response blocks must be an array.")
    answers, extras = [], []
    for block in blocks:
        if not isinstance(block, dict):
            extras.extend(["### Unrecognized response block", "", fence(block), ""])
            warnings.append(f"Turn {number}: unrecognized block preserved as JSON.")
            continue
        handled = False
        for kind in ("markdown_block", "text_block", "code_block"):
            if kind not in block:
                continue
            value = block[kind]
            if isinstance(value, dict):
                content = first_text(value, ("answer", "text", "content", "code"))
            else:
                content = text_value(value)
            if content:
                if kind == "code_block":
                    content = fence(content, str(value.get("language", "")) if isinstance(value, dict) else "")
                # Keep every content block in its received order, including
                # alternative answers, rather than dropping later blocks.
                answers.append(content)
                lines.extend([content, ""])
                handled = True
        for key in ("plan_block", "reasoning_plan_block", "reasoning_block"):
            if block.get(key) is not None:
                lines.extend(["### Plan / reasoning returned by Perplexity", "", fence(block[key]), ""])
                handled = True
        for key in ("web_result_block", "web_citation_block", "citation_block"):
            if key not in block:
                continue
            value = block[key]
            sources = value.get("web_results", value.get("citations")) if isinstance(value, dict) else None
            if isinstance(sources, list) and all(isinstance(s, dict) for s in sources):
                lines.extend(["### Sources", ""])
                for index, source in enumerate(sources, 1):
                    url = source.get("url", "")
                    name = source.get("name") or source.get("title") or url or "Source"
                    try:
                        is_http = isinstance(url, str) and urlsplit(url).scheme in {"http", "https"}
                    except ValueError:
                        is_http = False
                        warnings.append(f"Turn {number}: malformed source URL; original retained in raw JSON.")
                    if is_http:
                        target = url.replace("<", "%3C").replace(">", "%3E").replace("\n", "%0A").replace("\r", "%0D")
                        lines.append(f"{index}. [{md_label(name)}](<{target}>)")
                    else:
                        lines.append(f"{index}. {md_label(name)}")
                    if source.get("snippet"):
                        lines.extend(["", text_value(source["snippet"])])
                lines.append("")
                handled = True
        known = {"markdown_block", "text_block", "code_block", "plan_block", "reasoning_plan_block",
                 "reasoning_block", "web_result_block", "web_citation_block", "citation_block"}
        unknown = [k for k in block if k.endswith("_block") and k not in known]
        if not handled or unknown:
            extras.extend(["### Additional response data", "", fence(block), ""])
            warnings.append(f"Turn {number}: additional block data preserved as JSON; check rendering.")
    legacy = entry.get("text")
    if isinstance(legacy, str) and legacy.lstrip().startswith("{"):
        try:
            parsed = json.loads(legacy)
            if isinstance(parsed, dict) and parsed.get("answer"):
                legacy = parsed["answer"]
                remaining = {k: v for k, v in parsed.items() if k != "answer"}
                if remaining:
                    extras.extend(["### Additional legacy response data", "", fence(remaining), ""])
            else:
                legacy = fence(parsed)
                warnings.append(f"Turn {number}: legacy text JSON preserved without a known answer field.")
        except ValueError:
            pass
    legacy_answer = first_text(entry, ("answer",)) or text_value(legacy)
    if legacy_answer and legacy_answer not in answers:
        lines.extend([legacy_answer, ""])
        answers.append(legacy_answer)
    if not answers:
        lines.extend(["*(No recognized answer text. Inspect the additional data and raw JSON.)*", ""])
        warnings.append(f"Turn {number}: no recognized answer text.")
    for key in ("attachments", "file_attachments", "uploaded_files", "files"):
        if entry.get(key):
            lines.extend(["### Attachment references", "", fence(entry[key]), ""])
    lines.extend(extras)
    return "\n".join(lines)


def normalize_raw(raw, fallback_id=None, list_meta=None):
    """Accept our pages, the older REST format, and the earlier CLI envelope."""
    warnings, meta = [], dict(list_meta or {})
    if not isinstance(raw, dict):
        raise ExportError("Unrecognized raw format; expected an object.")
    if raw.get("format") == IMPORT_FORMAT:
        uid = valid_id(raw.get("uuid"))
        if fallback_id and fallback_id != uid:
            raise ExportError("Imported snapshot identity differs from filename.")
        saved_meta = raw.get("list_metadata", {})
        if not isinstance(saved_meta, dict) or not isinstance(raw.get("original"), dict):
            raise ExportError("Invalid imported snapshot envelope.")
        return normalize_raw(raw["original"], uid, {**meta, **saved_meta})
    if raw.get("format") == FORMAT:
        if raw.get("pagination_complete") is not True:
            raise ExportError("This is an incomplete snapshot; refusing to label it a full transcript.")
        pages = raw.get("pages")
        if not isinstance(pages, list) or not pages:
            raise ExportError("Snapshot has no response pages.")
        for i, page in enumerate(pages):
            validate_entries(page)
            if page.get("has_next_page") is not (i < len(pages) - 1):
                raise ExportError("Snapshot pagination flags do not match its saved pages.")
        extra = raw.get("list_metadata", {})
        if not isinstance(extra, dict):
            raise ExportError("Invalid saved listing metadata.")
        meta.update(extra)
        meta["uuid"] = valid_id(raw.get("uuid"))
        entries = join_entries(pages)
        for page in pages:
            page_meta = page.get("thread_metadata", {})
            if not isinstance(page_meta, dict):
                raise ExportError("Invalid thread metadata.")
            page_id = page_meta.get("uuid") or page_meta.get("context_uuid")
            if page_id and page_id != raw["uuid"]:
                raise ExportError("Response thread identity differs from the requested thread.")
            meta.update(page_meta)
    elif "meta" in raw and "turns" in raw:
        if not isinstance(raw["meta"], dict) or not isinstance(raw["turns"], list):
            raise ExportError("Invalid CLI snapshot.")
        meta.update(raw["meta"])
        entries = raw["turns"]
        if any(not isinstance(e, dict) for e in entries):
            raise ExportError("Invalid CLI turn.")
        warnings.append("Imported CLI snapshot: retrieval completeness cannot be verified from this format.")
    elif "entries" in raw:
        entries = validate_entries(raw)
        if raw.get("has_next_page") is True:
            raise ExportError("Legacy raw file still reports more pages; it is incomplete.")
        page_meta = raw.get("thread_metadata") or {}
        if not isinstance(page_meta, dict):
            raise ExportError("Invalid legacy thread metadata.")
        meta.update(page_meta)
        if raw.get("uuid") and not (meta.get("uuid") or meta.get("context_uuid")):
            meta["uuid"] = raw["uuid"]
        warnings.append("Imported legacy snapshot: original pagination and dropped fields cannot be verified.")
    else:
        raise ExportError("Unknown raw schema; no empty transcript was created.")
    if not (meta.get("uuid") or meta.get("context_uuid") or meta.get("session_id")):
        meta["uuid"] = valid_id(fallback_id)
    uid = identifier(meta)
    if fallback_id and uid != fallback_id:
        raise ExportError("Filename/request identity disagrees with the saved thread identity.")
    if not entries:
        warnings.append("No conversation entries were returned.")
    return uid, meta, entries, warnings


def render(raw, fallback_id=None, list_meta=None):
    uid, meta, entries, warnings = normalize_raw(raw, fallback_id, list_meta)
    title = str(meta.get("title") or (entries[0].get("thread_title") if entries else "") or "Untitled conversation")
    title = title.replace("\r", " ").replace("\n", " ")
    url = meta.get("url") or ("/search/" + str(meta.get("slug") or uid))
    if isinstance(url, str) and url.startswith("/"):
        url = BASE + url
    lines = [f"# {title}", "", f"- Thread ID: {uid}", f"- URL: {url}",
             f"- Created: {meta.get('created_at') or 'Not provided'}",
             f"- Updated: {meta.get('updated_at') or meta.get('last_query_datetime') or 'Not provided'}",
             f"- Conversation turns: {len(entries)}", ""]
    for number, entry in enumerate(entries, 1):
        lines.extend(["---", "", render_entry(entry, number, warnings)])
    if warnings:
        lines.extend(["---", "", "## Export notes", "", *["- " + w for w in dict.fromkeys(warnings)], ""])
    row = {"uuid": uid, "title": title, "turns": len(entries),
           "updated": str(meta.get("updated_at") or meta.get("last_query_datetime") or meta.get("created_at") or ""),
           "warnings": list(dict.fromkeys(warnings)), "exported_at": now()}
    return uid, "\n".join(lines).rstrip() + "\n", row


def export_one(root, raw, fallback_id, list_meta=None):
    uid, markdown, row = render(raw, fallback_id, list_meta)
    # Identity-based filenames remain stable after a title/date change, and
    # cannot collide on an eight-character UUID prefix or Windows device name.
    row["filename"] = f"markdown/chat_{uid}.md"
    archived = raw
    if raw.get("format") not in {FORMAT, IMPORT_FORMAT}:
        archived = {"format": IMPORT_FORMAT, "uuid": uid, "imported_at": now(),
                    "list_metadata": list_meta or {}, "original": raw}
    atomic_json(inside(root, f"raw/{uid}.json"), archived)
    atomic_text(inside(root, row["filename"]), markdown)
    atomic_json(inside(root, f"records/{uid}.json"), row)
    return row


def save_summary(root, report):
    rows = []
    for path in sorted(inside(root, "records").glob("*.json")):
        row = read_json(path)
        identifier(row)
        if not inside(root, row["filename"]).is_file():
            report["warnings"].append("A previous Markdown file is missing. Rerun or rebuild from raw.")
        rows.append(row)
    rows.sort(key=lambda r: (r.get("updated", ""), r["uuid"]), reverse=True)
    text = ["# Perplexity conversation exports", "", "Each link opens a local Markdown transcript.",
            "Existing exports are retained across runs; consult export_report.json for the latest run's status.", "",
            "| Conversation | Updated | Turns |", "| --- | --- | ---: |"]
    for row in rows:
        text.append(f"| [{md_label(row['title'])}]({row['filename']}) | {md_label(row.get('updated', ''))} | {row['turns']} |")
    atomic_text(inside(root, "INDEX.md"), "\n".join(text) + "\n")
    report["finished_at"] = now()
    report["status"] = "interrupted" if report.get("interrupted") else ("needs_review" if report["errors"] or report["warnings"] else "completed_requested_scope")
    atomic_json(inside(root, "export_report.json"), report)
    atomic_json(inside(root, f"runs/{report['run_id']}/report.json"), report)


def legacy_metadata(directory):
    found = {}
    # Old exporters needed the separate list to recover title, space and ID.
    for base in (directory.parent, directory):
        for name in ("thread_index.json", "thread_list.json"):
            path = base / name
            if path.is_file():
                rows, _ = list_items(read_json(path))
                found.update({identifier(row): row for row in rows})
    return found


def run_offline(args, root, report):
    directory = Path(args.from_raw).expanduser().resolve()
    if not directory.is_dir():
        raise ExportError("--from-raw must name an existing raw JSON directory.")
    metas = legacy_metadata(directory)
    skip = {"thread_index.json", "thread_list.json", "manifest.json", "index.json", "export_report.json"}
    paths = [p for p in sorted(directory.glob("*.json")) if p.name not in skip]
    if args.limit:
        paths = paths[:args.limit]
    if not paths:
        raise ExportError("No raw conversation JSON files found.")
    report["selected"] = len(paths)
    seen = set()
    for n, path in enumerate(paths, 1):
        print(f"[{n}/{len(paths)}] Rebuilding local transcript.")
        try:
            uid = valid_id(path.stem)
            raw = read_json(path)
            actual, _, _, _ = normalize_raw(raw, uid, metas.get(uid))
            if actual in seen:
                raise ExportError("Duplicate thread identity in input directory.")
            seen.add(actual)
            row = export_one(root, raw, uid, metas.get(uid))
            report["exported"] += 1
            report["warnings"].extend(f"{uid}: {w}" for w in row["warnings"])
        except ExportError as exc:
            report["errors"].append({"input": path.name, "error": str(exc)})
            print(f"  Failed: {exc}")


def run_live(args, root, report, client_factory=None):
    try:
        cookie = Path(args.cookies).expanduser().read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        raise ExportError("Cannot read cookie file. See the setup steps in README.md.") from None
    client = (client_factory or Client)(cookie, args.delay)
    run_dir = inside(root, f"runs/{report['run_id']}")

    def checkpoint(kind, number, payload):
        valid_id(kind)
        atomic_json(inside(run_dir, f"received/{kind}/{number:05d}.json"), payload)

    try:
        if args.thread_id or args.thread_url:
            threads = select_threads(client, args.thread_id, args.thread_url, checkpoint)
            report["discovery"] = "explicit_thread_selection"
        else:
            threads = fetch_index(client, checkpoint)
            report["discovery"] = "paginated_listing_plus_recent"
            # Supplemental discovery only: the recent list is never presented
            # as a replacement for full listing pagination.
            try:
                recent = client.request("GET", api_path("list_recent", [("exclude_asi", "false")]))
                checkpoint("recent", 0, recent)
                extras, _ = list_items(recent)
                by_id = {identifier(t): t for t in threads}
                for row in extras:
                    by_id.setdefault(identifier(row), row)
                threads = list(by_id.values())
            except AuthError:
                raise
            except ExportError as exc:
                report["warnings"].append("Supplemental recent-list check failed: " + str(exc))
        atomic_json(inside(run_dir, "thread_list.json"), threads)
        report["discovered"] = len(threads)
        if args.limit:
            threads = threads[:args.limit]
        report["selected"] = len(threads)
        if not threads:
            report["warnings"].append("No conversations discovered; verify that the authenticated account is correct.")
        for n, meta in enumerate(threads, 1):
            uid = identifier(meta)
            print(f"[{n}/{len(threads)}] Fetching conversation {uid}.")
            try:
                raw = fetch_detail(client, uid, meta, checkpoint)
                # Checkpointed responses remain available if rendering fails;
                # an unvalidated snapshot never replaces an earlier good one.
                row = export_one(root, raw, uid)
                report["exported"] += 1
                report["warnings"].extend(f"{uid}: {w}" for w in row["warnings"])
                # These exact response pages are now retained in raw/<id>.json.
                shutil.rmtree(inside(run_dir, f"received/{uid}"))
            except AuthError:
                raise
            except ExportError as exc:
                report["errors"].append({"uuid": uid, "error": str(exc)})
                print(f"  Failed: {exc}")
    finally:
        close_client(client, report)


def nonnegative_int(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("Must be nonnegative.")
    return number


def nonnegative_float(value):
    number = float(value)
    if number < 0 or not math.isfinite(number):
        raise argparse.ArgumentTypeError("Must be a finite nonnegative number.")
    return number


def parser():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-o", "--output", default=str(SCRIPT_DIR / "pplx_export"))
    p.add_argument("-c", "--cookies", default=str(SCRIPT_DIR / "pplx_cookies.txt"))
    p.add_argument("--from-raw", help="Rebuild Markdown from this raw JSON directory; no login or network.")
    p.add_argument("--limit", type=nonnegative_int, default=0, help="Limit conversations for a smoke test; 0 means all discovered.")
    p.add_argument("--delay", type=nonnegative_float, default=1.0, help="Minimum seconds between API requests.")
    p.add_argument("--thread-id", action="append", default=[], help="Export just this thread ID; repeat for several.")
    p.add_argument("--thread-url", action="append", default=[], help="Select an HTTPS Perplexity thread URL; UUID paths work directly, slugs must resolve through account history.")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.from_raw and (args.thread_id or args.thread_url):
        parser().error("--from-raw cannot be combined with thread IDs or URLs")
    root = Path(args.output).expanduser().resolve()
    report = {"format": FORMAT, "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8],
              "started_at": now(), "mode": "offline" if args.from_raw else "live",
              "scope": "limited" if args.limit else ("specified_threads" if args.thread_id or args.thread_url else "all_discovered"),
              "account_completeness": "not_verified", "exported": 0, "errors": [], "warnings": []}
    locked = False
    lock = root / ".export.lock"
    try:
        root.mkdir(parents=True, exist_ok=True)
        try:
            lock.mkdir()
            locked = True
        except FileExistsError:
            raise ExportError("Another export may be running in this folder. See README for stale-lock recovery.") from None
        for uid in args.thread_id:
            valid_id(uid)
        for url in args.thread_url:
            parse_thread_url(url)
        print(f"Saving Markdown locally to: {root / 'markdown'}")
        if args.from_raw:
            run_offline(args, root, report)
        else:
            run_live(args, root, report)
    except KeyboardInterrupt:
        report["interrupted"] = True
        report["errors"].append({"error": "Interrupted; prior exports and received pages retained."})
        print("Interrupted. Completed files are retained.")
    except (ExportError, OSError) as exc:
        message = str(exc) if isinstance(exc, ExportError) else "Local file operation failed; check disk space and permissions."
        report["errors"].append({"error": message})
        print(f"Export stopped: {message}", file=sys.stderr)
    finally:
        if locked:
            try:
                save_summary(root, report)
            except (ExportError, OSError, KeyError, TypeError):
                report["errors"].append({"error": "Could not save index/report. Completed transcript files may still be available."})
                print("Could not save the final index/report.", file=sys.stderr)
            if not release_lock(lock):
                report["errors"].append({"error": "Could not release the export lock; verify no exporter is running before removing it."})
                try:
                    save_summary(root, report)
                except (ExportError, OSError, KeyError, TypeError):
                    print("Could not record the export-lock cleanup failure.", file=sys.stderr)
    print(f"Exported: {report['exported']}; errors: {len(report['errors'])}; review notes: {len(report['warnings'])}.")
    if report["warnings"]:
        print("Review export_report.json and the export notes in affected transcripts.")
    if not args.from_raw:
        print("Account-wide coverage is unverified: compare old/long/special conversations with the website.")
    return 130 if report.get("interrupted") else (1 if report["errors"] or report["warnings"] else 0)


if __name__ == "__main__":
    sys.exit(main())
