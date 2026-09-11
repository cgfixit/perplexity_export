#!/usr/bin/env python3
"""DOM virtual-scroll export of a Perplexity shared thread (optional Playwright).

This is the share-fidelity path: it drives Chromium, scrolls the live conversation
pane, and extracts ordered user/assistant turns as rendered. It is deliberately
separate from public_verify.py --native-export (Perplexity's /rest/thread/export
Markdown API), which can omit early turns on long shares.

Install (optional; not required for default unit tests / CI without browsers):
    pip install -r requirements-dom.txt
    playwright install chromium

Example:
    python dom_export.py --thread-url URL -o out.md --report out.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import pplx_export as exporter


SCROLL_ROOT_SELECTOR = ".scrollable-container"
USER_BUBBLE_SELECTOR = ".group\\/user-bubble, [class*='group/user-bubble']"
ASSISTANT_TEXT_SELECTOR = ".group\\/final-text, [class*='group/final-text']"
TRUNCATION_RE = re.compile(r"Sorry,\s*could not load the rest of the thread", re.I)
PRIVATE_RE = re.compile(
    r"This session is private|Sign in if you are the owner of this session|to request access",
    re.I,
)
SHARED_RE = re.compile(r"Viewing a shared session", re.I)
BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.I)
CONTINUATION_HINT_RE = re.compile(r"(?:part\s*2|continuation|continued)", re.I)


class DomExportError(exporter.ExportError):
    """DOM share export failed without claiming completeness."""


def clean_text(value: str) -> str:
    return BIDI_RE.sub("", value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def turns_to_markdown(turns, report) -> str:
    """Render extracted DOM turns as ordinary Markdown (no secrets)."""
    lines = []
    title = (report.get("title") or "").strip()
    if title:
        lines.append(f"# {title}")
        lines.append("")
    lines.append(f"<!-- dom_export access={report.get('access')} turns_seen={report.get('turns_seen')} "
                 f"scroll_exhausted={report.get('scroll_exhausted')} "
                 f"ui_truncation_banner={json.dumps(report.get('ui_truncation_banner'))} -->")
    lines.append("")
    for index, turn in enumerate(turns, start=1):
        role = turn.get("role") or "unknown"
        heading = "User" if role == "user" else "Assistant" if role == "assistant" else role.title()
        lines.append(f"## Turn {index} ({heading})")
        lines.append("")
        text = clean_text(turn.get("text") or "")
        if text:
            lines.append(text)
            lines.append("")
        attachments = turn.get("attachments") or []
        if attachments:
            lines.append("Attachments:")
            for name in attachments:
                lines.append(f"- {name}")
            lines.append("")
    if report.get("continuation_urls"):
        lines.append("## Continuation links observed in the share UI")
        lines.append("")
        for url in report["continuation_urls"]:
            lines.append(f"- {url}")
        lines.append("")
    if report.get("ui_truncation_banner"):
        lines.append("## UI truncation")
        lines.append("")
        lines.append(f"> {report['ui_truncation_banner']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _require_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise DomExportError(
            "Playwright is not installed. Install the optional DOM extra with "
            "`pip install -r requirements-dom.txt` then `playwright install chromium`."
        ) from exc
    return sync_playwright


def _dismiss_overlays(page) -> None:
    """Dismiss cookie/login chrome anonymously; never click Sign in / OAuth.

    Do not click a generic Close control: on private shares that dismisses the
    owner gate and navigates to the empty home shell.
    """
    # Cookie consent only (scoped when possible).
    for sel in (
        '[data-testid="consent-dialog"] button:has-text("Accept All")',
        '[data-testid="consent-dialog"] button:has-text("Accept all")',
        '[data-testid="consent-dialog"] button:has-text("Agree")',
        '[data-testid="consent-dialog"] button:has-text("Got it")',
        '[data-testid="consent-dialog"] button:has-text("Necessary only")',
        'button:has-text("Accept All")',
        'button:has-text("Accept all")',
        'button:has-text("Necessary only")',
        'button:has-text("Reject non-essential")',
    ):
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible(timeout=400):
                loc.first.click(timeout=1500)
                page.wait_for_timeout(200)
                break
        except Exception:
            continue
    # Close only an explicit login modal, never the private-session gate.
    for sel in (
        '[data-testid="login-modal"] button[aria-label="Close"]',
        '[data-testid="login-modal"] [aria-label="Close"]',
    ):
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible(timeout=400):
                loc.first.click(timeout=1500)
                page.wait_for_timeout(200)
                break
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass


def _expand_truncated_queries(page) -> None:
    for sel in (
        '[data-testid="toggle-query-expand-button"]',
        'button:has-text("Read more")',
        'button:has-text("Show more")',
    ):
        try:
            loc = page.locator(sel)
            count = min(loc.count(), 50)
            for index in range(count):
                button = loc.nth(index)
                try:
                    if button.is_visible(timeout=200):
                        button.click(timeout=1500)
                        page.wait_for_timeout(150)
                except Exception:
                    continue
        except Exception:
            continue


def _scroll_thread_until_stable(page, max_steps: int = 80, settle_ms: int = 350) -> bool:
    """Virtually scroll the conversation container until height/position stabilize."""
    root = page.locator(SCROLL_ROOT_SELECTOR)
    if not root.count():
        # Fall back to document scrolling when the SPA shell differs.
        last = None
        stable = 0
        for _ in range(max_steps):
            metrics = page.evaluate(
                """() => ({top: window.scrollY, height: document.documentElement.scrollHeight})"""
            )
            page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight * 0.9))")
            page.wait_for_timeout(settle_ms)
            key = (metrics["top"], metrics["height"])
            if key == last:
                stable += 1
                if stable >= 3:
                    return True
            else:
                stable = 0
                last = key
        return False

    last = None
    stable = 0
    for _ in range(max_steps):
        metrics = page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return null;
                return {top: el.scrollTop, height: el.scrollHeight, client: el.clientHeight};
            }""",
            SCROLL_ROOT_SELECTOR,
        )
        if not metrics:
            return False
        page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (!el) return;
                el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + Math.max(500, el.clientHeight * 0.9));
            }""",
            SCROLL_ROOT_SELECTOR,
        )
        page.wait_for_timeout(settle_ms)
        key = (metrics["top"], metrics["height"])
        at_bottom = metrics["top"] + metrics["client"] >= metrics["height"] - 4
        if key == last or at_bottom:
            stable += 1
            if stable >= 3:
                # One more settle pass for late mounts.
                page.wait_for_timeout(settle_ms * 2)
                return True
        else:
            stable = 0
            last = key
    return False


def _extract_turns(page) -> list:
    """Collect ordered user/assistant turns currently mounted in the DOM."""
    payload = page.evaluate(
        """() => {
            const clean = (s) => (s || '').replace(/[\\u202a-\\u202e\\u2066-\\u2069]/g, '').trim();
            const root = document.querySelector('.scrollable-container') || document.body;
            const nodes = [...root.querySelectorAll("[class*='group/user-bubble'], [class*='group/final-text']")];
            // Dedupe nested matches: prefer the outermost matching node.
            const filtered = nodes.filter(el => !nodes.some(other => other !== el && other.contains(el)));
            const turns = [];
            for (const el of filtered) {
                const cls = (el.className || '').toString();
                const isUser = cls.includes('user-bubble');
                const isAssistant = cls.includes('final-text');
                if (!isUser && !isAssistant) continue;
                const text = clean(el.innerText || '');
                if (!text) continue;
                const attachments = [];
                for (const img of el.querySelectorAll('img[alt], img[src]')) {
                    const name = clean(img.getAttribute('alt') || '');
                    if (name && !attachments.includes(name)) attachments.push(name);
                }
                // Filename chips sometimes appear as plain text near images.
                const fileHit = text.match(/\\b([\\w .-]+\\.(?:jpg|jpeg|png|gif|webp|pdf))\\b/i);
                if (fileHit && !attachments.includes(fileHit[1])) attachments.push(fileHit[1]);
                turns.push({
                    role: isUser ? 'user' : 'assistant',
                    text,
                    attachments,
                });
            }
            return turns;
        }"""
    )
    return payload or []


def _host_allowed(host: str) -> bool:
    """Exact registrable host or subdomain (avoid evilcgfixit.com false matches)."""
    host = (host or "").lower().split("@")[-1].split(":")[0].strip(".")
    if not host:
        return False
    allowed = ("perplexity.ai", "cgfixit.com")
    return any(host == root or host.endswith("." + root) for root in allowed)


def _continuation_urls(page_text: str) -> list:
    found = []
    seen = set()
    for match in URL_RE.finditer(page_text or ""):
        url = match.group(0).rstrip(".,;")
        host = urlsplit(url).netloc.lower()
        if not _host_allowed(host):
            continue
        # Prefer links near Part 2 / continuation wording; always keep cgfixit redirects.
        start = max(0, match.start() - 80)
        window = page_text[start: match.end() + 40]
        bare = host.split(":")[0].lstrip(".").lower()
        is_cgfixit = bare == "cgfixit.com" or bare.endswith(".cgfixit.com")
        if CONTINUATION_HINT_RE.search(window) or is_cgfixit:
            if url not in seen:
                seen.add(url)
                found.append(url)
    return found


def _classify_access(page_text: str, turns: list) -> str:
    if PRIVATE_RE.search(page_text or ""):
        return "private"
    if turns or SHARED_RE.search(page_text or ""):
        return "public"
    if re.search(r"could not|access denied|sign in to continue", page_text or "", re.I):
        return "denied"
    if not turns:
        return "denied"
    return "public"


def export_share(thread_url: str, headless: bool = True, timeout_ms: int = 60000) -> tuple[str, dict]:
    """Open a share URL, scroll, extract turns, and return (markdown, report)."""
    uid = exporter.resolve_share_thread_id(thread_url)
    # Prefer the UUID form so title-slug redirects do not change the loaded page.
    url = f"https://www.perplexity.ai/search/{uid}"
    sync_playwright = _require_playwright()

    report = {
        "thread_uuid": uid,
        "thread_url": url,
        "source_url": thread_url,
        "access": "denied",
        "turns_seen": 0,
        "scroll_exhausted": False,
        "ui_truncation_banner": None,
        "continuation_urls": [],
        "title": "",
        "engine": "playwright-chromium",
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                locale="en-US",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(3000)
            body = clean_text(page.inner_text("body"))
            report["title"] = clean_text(page.title())
            if PRIVATE_RE.search(body):
                report["access"] = "private"
                report["scroll_exhausted"] = True
                markdown = turns_to_markdown([], report)
                return markdown, report

            _dismiss_overlays(page)
            page.wait_for_timeout(1200)
            body = clean_text(page.inner_text("body"))
            report["title"] = clean_text(page.title()) or report["title"]
            if PRIVATE_RE.search(body):
                report["access"] = "private"
                report["scroll_exhausted"] = True
                markdown = turns_to_markdown([], report)
                return markdown, report

            report["scroll_exhausted"] = _scroll_thread_until_stable(page)
            _expand_truncated_queries(page)
            # Scroll back toward the top so early turns remount if virtualized.
            page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (el) el.scrollTop = 0;
                    else window.scrollTo(0, 0);
                }""",
                SCROLL_ROOT_SELECTOR,
            )
            page.wait_for_timeout(800)
            _expand_truncated_queries(page)

            # Collect while scrolling down again so virtualized nodes are visited.
            ordered = []
            seen_keys = set()

            def ingest(batch):
                for turn in batch:
                    key = (turn.get("role"), (turn.get("text") or "")[:240])
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    ordered.append(turn)

            ingest(_extract_turns(page))
            root = page.locator(SCROLL_ROOT_SELECTOR)
            steps = 60 if root.count() else 30
            for _ in range(steps):
                if root.count():
                    page.evaluate(
                        """(sel) => {
                            const el = document.querySelector(sel);
                            if (!el) return;
                            el.scrollTop = Math.min(el.scrollHeight,
                                el.scrollTop + Math.max(500, el.clientHeight * 0.9));
                        }""",
                        SCROLL_ROOT_SELECTOR,
                    )
                else:
                    page.evaluate("window.scrollBy(0, Math.max(600, window.innerHeight * 0.9))")
                page.wait_for_timeout(280)
                _expand_truncated_queries(page)
                ingest(_extract_turns(page))

            body = clean_text(page.inner_text("body"))
            trunc = TRUNCATION_RE.search(body)
            if trunc:
                report["ui_truncation_banner"] = "Sorry, could not load the rest of the thread"
                report["scroll_exhausted"] = True
            report["continuation_urls"] = _continuation_urls(body)
            report["turns_seen"] = len(ordered)
            report["access"] = _classify_access(body, ordered)
            if not report["title"]:
                report["title"] = clean_text(page.title())
            markdown = turns_to_markdown(ordered, report)
            return markdown, report
        finally:
            browser.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread-url", required=True, help="HTTPS Perplexity UUID or slug share URL")
    parser.add_argument("-o", "--output", type=Path, help="Write Markdown transcript here")
    parser.add_argument("--report", type=Path, help="Write JSON completeness report here")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--timeout-ms", type=int, default=60000)
    args = parser.parse_args(argv)

    try:
        markdown, report = export_share(
            args.thread_url,
            headless=not args.headed,
            timeout_ms=args.timeout_ms,
        )
    except DomExportError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    except exporter.ExportError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception:
        # Avoid leaking page HTML / URLs from unexpected driver errors.
        print("FAIL: DOM share export failed (browser, challenge, or UI change).", file=sys.stderr)
        return 1

    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        f"DOM_EXPORT: access={report['access']} turns_seen={report['turns_seen']} "
        f"scroll_exhausted={report['scroll_exhausted']} "
        f"ui_truncation_banner={bool(report['ui_truncation_banner'])} "
        f"continuation_urls={len(report['continuation_urls'])}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
