#!/usr/bin/env python3
"""DOM virtual-scroll export of a Perplexity shared thread (optional Playwright).

This is the fail-closed UI-fidelity path: it drives Chromium, scrolls the live
conversation pane, and extracts ordered user/assistant turns as rendered. It is
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
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pplx_export as exporter


SCROLL_ROOT_SELECTOR = ".scrollable-container"
USER_BUBBLE_SELECTOR = ".group\\/user-bubble, [class*='group/user-bubble']"
ASSISTANT_TEXT_SELECTOR = ".group\\/final-text, [class*='group/final-text']"
COPY_BUTTON_SELECTOR = 'button[data-dom-export-copy="true"]'
ISOLATED_CLIPBOARD_SCRIPT = r"""(() => {
    const state = {text: '', sequence: 0};
    Object.defineProperty(window, '__domExportClipboard', {value: state});
    Object.defineProperty(navigator, 'clipboard', {
        configurable: false,
        value: {
            __domExportIsolated: true,
            writeText: async value => { state.text = String(value); state.sequence += 1; },
            write: async items => {
                let captured = '';
                for (const item of items || []) {
                    const types = item.types || [];
                    if (!types.includes('text/plain') || typeof item.getType !== 'function') continue;
                    captured = await (await item.getType('text/plain')).text();
                    break;
                }
                state.text = String(captured);
                state.sequence += 1;
            },
            readText: async () => state.text,
        },
    });
})()"""
TRUNCATION_RE = re.compile(r"Sorry,\s*could not load the rest of the thread", re.I)
PRIVATE_RE = re.compile(
    r"This session is private|Sign in if you are the owner of this session|to request access",
    re.I,
)
SHARED_RE = re.compile(r"Viewing a shared session", re.I)
BIDI_RE = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
URL_RE = re.compile(r"https?://[^\s)\]>\"']+", re.I)
CONTINUATION_HINT_RE = re.compile(r"(?:part\s*2|continuation|continued)", re.I)
PROFILE_MARKER = ".perplexity-export-dom-profile"
PROFILE_MARKER_TEXT = "Dedicated perplexity_export DOM profile. Contains private browser state.\n"


class DomExportError(exporter.ExportError):
    """DOM share export failed without claiming completeness."""


def clean_text(value: str) -> str:
    return BIDI_RE.sub("", value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _validate_live_page_url(page_url: str, expected_thread_id: str) -> None:
    try:
        parsed = urlsplit(page_url)
        resolved = exporter.resolve_share_thread_id(page_url)
        port = parsed.port
    except (exporter.ExportError, ValueError) as exc:
        raise DomExportError("The browser left the validated Perplexity thread URL.") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname != "www.perplexity.ai"
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or resolved != expected_thread_id
    ):
        raise DomExportError("The browser left the validated Perplexity thread URL.")


def _content_tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", clean_text(value)).casefold()
    normalized = re.sub(
        r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])",
        " ",
        normalized,
    )
    return re.findall(r"[^\W_]+", normalized)


def _markdown_visible_tokens(value: str) -> list[str]:
    # Link destinations are metadata, not evidence that the visible answer text
    # was copied. Preserve labels and bare/autolink URLs that are themselves visible.
    value = re.sub(r"(?m)^\s*\[[^\]\n]+\]:\s*\S+.*$", "", value)
    value = re.sub(r"(?<=\])\((?:\\.|[^()\n]|\([^()\n]*\))*\)", "", value)
    value = re.sub(r"(?<=\])\[[^\]\n]*\]", "", value)
    return _content_tokens(value)


def _validate_markdown_payload(value: str, features: dict, dom_text: str = "") -> None:
    links = list(dict.fromkeys(features.get("links") or []))
    if links and "](http" in value:
        missing = [url for url in links if url not in value and url.rstrip("/") not in value]
        if missing:
            raise DomExportError("Answer Copy omitted a rendered link target.")
    elif links:
        raise DomExportError("Answer Copy returned text without rendered Markdown links.")
    code_blocks = int(features.get("code_blocks") or 0)
    fence_lines = len(re.findall(r"(?m)^\s*(?:`{3,}|~{3,})", value))
    if code_blocks and fence_lines < code_blocks * 2:
        raise DomExportError("Answer Copy returned text without rendered code fences.")
    tables = int(features.get("tables") or 0)
    table_delimiters = len(re.findall(
        r"(?m)^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$",
        value,
    ))
    if tables and table_delimiters < tables:
        raise DomExportError("Answer Copy returned text without rendered table markup.")
    headings = int(features.get("headings") or 0)
    if headings and len(re.findall(r"(?m)^#{1,6}\s+", value)) < headings:
        raise DomExportError("Answer Copy returned text without rendered heading markup.")
    visible_tokens = _content_tokens(dom_text)
    copied_tokens = _markdown_visible_tokens(value)
    cursor = 0
    for token in copied_tokens:
        if cursor < len(visible_tokens) and token == visible_tokens[cursor]:
            cursor += 1
    if visible_tokens and cursor != len(visible_tokens):
        raise DomExportError("Answer Copy omitted visible rendered content.")
    if not visible_tokens and clean_text(dom_text) and clean_text(dom_text) not in clean_text(value):
        raise DomExportError("Answer Copy omitted visible rendered content.")


def _prepare_profile_dir(profile_dir: Path) -> Path:
    path = profile_dir.expanduser().resolve()
    marker = path / PROFILE_MARKER
    if path.exists() and not path.is_dir():
        raise DomExportError("The dedicated browser profile path is not a directory.")
    if path.exists():
        entries = list(path.iterdir())
        if entries and (
            marker.is_symlink()
            or not marker.is_file()
            or marker.read_text(encoding="utf-8") != PROFILE_MARKER_TEXT
        ):
            raise DomExportError(
                "Refusing an existing unmarked browser profile; choose a new empty directory."
            )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        path.chmod(0o700)
    if not marker.exists():
        exporter.atomic_text(marker, PROFILE_MARKER_TEXT)
    return path


def _install_navigation_guard(page, expected_thread_id: str):
    state = {"blocked": False}

    def guard(route, request):
        if request.is_navigation_request() and request.frame == page.main_frame:
            try:
                _validate_live_page_url(request.url, expected_thread_id)
            except DomExportError:
                state["blocked"] = True
                route.abort()
                return
            response = route.fetch(max_redirects=0)
            if 300 <= response.status < 400:
                state["blocked"] = True
                route.abort()
                return
            route.fulfill(response=response)
            return
        route.continue_()

    page.route("**/*", guard)
    return guard, state


def turns_to_markdown(turns, report) -> str:
    """Render extracted DOM turns as ordinary Markdown (no secrets)."""
    lines = []
    title = (report.get("title") or "").strip()
    if title:
        lines.append(f"# {title}")
        lines.append("")
    lines.append(f"<!-- dom_export access={report.get('access')} turns_seen={report.get('turns_seen')} "
                 f"status={report.get('status')} stop_reason={json.dumps(report.get('stop_reason'))} "
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
            if turn.get("capture") == "dom_text_fallback":
                lines.append("> **Partial capture:** answer Copy did not yield verified Markdown; rendered text follows.")
                lines.append("")
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


def _dismiss_overlays(page, close_login: bool = True, pace_ms: int = 1000) -> None:
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
                loc.first.evaluate("element => element.click()")
                page.wait_for_timeout(pace_ms)
                break
        except Exception:
            continue
    if not close_login:
        return
    # Close only an explicit login modal, never the private-session gate.
    for sel in (
        '[data-testid="login-modal"] button[aria-label="Close"]',
        '[data-testid="login-modal"] [aria-label="Close"]',
    ):
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible(timeout=400):
                loc.first.evaluate("element => element.click()")
                page.wait_for_timeout(pace_ms)
                break
        except Exception:
            continue


def _expand_truncated_queries(page, pace_ms: int = 1000) -> int:
    expanded = 0
    for sel in (
        '[data-testid="toggle-query-expand-button"]',
        'button[aria-label="Expand query"]',
    ):
        try:
            loc = page.locator(sel)
            count = loc.count()
            for index in reversed(range(count)):
                button = loc.nth(index)
                if not button.is_visible(timeout=200):
                    continue
                if not button.is_enabled():
                    raise DomExportError("A visible prompt expansion control was disabled.")
                timeout_ms = min(10000, max(3000, pace_ms * 3))
                result = button.evaluate(
                    """async (element, timeoutMs) => {
                        const bubble = element.closest("[class*='group/user-bubble']");
                        if (!bubble) return {error: 'unscoped'};
                        const root = document.querySelector('.scrollable-container');
                        const absoluteTop = () => {
                            const rect = bubble.getBoundingClientRect();
                            const rootRect = root ? root.getBoundingClientRect() : {top: 0};
                            return Math.round(rect.top - rootRect.top + (root ? root.scrollTop : window.scrollY));
                        };
                        const controlSelector = '[data-testid="toggle-query-expand-button"], button[aria-label="Expand query"]';
                        const promptText = () => {
                            const clone = bubble.cloneNode(true);
                            for (const control of clone.querySelectorAll(controlSelector)) control.remove();
                            return (clone.textContent || '').trim();
                        };
                        const label = (element.getAttribute('aria-label') || element.innerText || '').trim();
                        const text = promptText();
                        const key = `${absoluteTop()}\u0000${text}`;
                        window.__domExportExpandedQueryKeys = window.__domExportExpandedQueryKeys || new Set();
                        const already = element.getAttribute('aria-expanded') === 'true'
                            || /show less|read less|collapse/i.test(label);
                        if (already || window.__domExportExpandedQueryKeys.has(key)) {
                            return {already: true};
                        }
                        element.click();
                        const deadline = performance.now() + timeoutMs;
                        while (performance.now() < deadline) {
                            if (!bubble.isConnected) return {expanded: false};
                            const liveControl = bubble.querySelector(controlSelector);
                            const currentLabel = liveControl
                                ? (liveControl.getAttribute('aria-label') || liveControl.innerText || '').trim()
                                : '';
                            if (promptText() !== text
                                || (liveControl && liveControl.getAttribute('aria-expanded') === 'true')
                                || /show less|read less|collapse/i.test(currentLabel)) {
                                const currentKey = `${absoluteTop()}\u0000${promptText()}`;
                                window.__domExportExpandedQueryKeys.add(currentKey);
                                return {expanded: true};
                            }
                            await new Promise(resolve => setTimeout(resolve, 100));
                        }
                        return {expanded: false};
                    }""",
                    timeout_ms,
                )
                if result.get("error"):
                    raise DomExportError("A prompt expansion control was outside a user turn.")
                if result.get("already"):
                    continue
                try:
                    if not result.get("expanded"):
                        raise DomExportError("A prompt remained truncated after its expansion click.")
                finally:
                    # This is a request/action throttle, not a readiness test.
                    page.wait_for_timeout(pace_ms)
                expanded += 1
        except DomExportError:
            raise
        except Exception as exc:
            raise DomExportError("A visible prompt could not be expanded safely.") from exc
    return expanded


SNAPSHOT_SCRIPT = r"""() => {
    const clean = (s) => (s || '').replace(/[\u202a-\u202e\u2066-\u2069]/g, '').trim();
    const customRoot = document.querySelector('.scrollable-container');
    const root = customRoot || document.scrollingElement || document.documentElement;
    const rootRect = customRoot
        ? customRoot.getBoundingClientRect()
        : {top: 0, bottom: window.innerHeight};
    const top = customRoot ? customRoot.scrollTop : window.scrollY;
    const height = customRoot ? customRoot.scrollHeight : document.documentElement.scrollHeight;
    const client = customRoot ? customRoot.clientHeight : window.innerHeight;
    const candidates = [...root.querySelectorAll("[class*='group/user-bubble'], [class*='group/final-text']")]
        .filter(el => el.classList.contains('group/user-bubble') || el.classList.contains('group/final-text'));
    // ponytail: mounted turn counts are small; replace O(n^2) only if the UI starts mounting thousands.
    const nodes = candidates.filter(el => !candidates.some(other => other !== el && other.contains(el)));
    const turns = [];
    window.__domExportTokenCounter = window.__domExportTokenCounter || 0;
    for (const el of nodes) {
        const role = el.classList.contains('group/user-bubble') ? 'user' : 'assistant';
        const directChildren = [...el.children];
        const toolbar = role === 'assistant'
            ? [...directChildren].reverse().find(child => child.querySelector('button[aria-label="Copy"]'))
            : null;
        const body = role === 'assistant'
            ? directChildren.find(child => child !== toolbar && clean(child.innerText))
            : el;
        const text = clean(body ? body.innerText : '');
        if (!text) continue;
        const rect = el.getBoundingClientRect();
        const margin = client * 0.5;
        if (rect.bottom < rootRect.top - margin || rect.top > rootRect.bottom + margin) continue;
        if (!el.dataset.domExportToken) {
            el.dataset.domExportToken = `dom-export-${++window.__domExportTokenCounter}`;
        }
        // No durable turn identifier was observed in the live share UI. Keep
        // this null until a specific attribute is verified across recycling.
        const identity = null;
        const attachments = [];
        for (const image of el.querySelectorAll('img[alt], img[src]')) {
            const name = clean(image.getAttribute('alt') || '');
            if (name && name.toLowerCase() !== 'attachment' && !attachments.includes(name)) attachments.push(name);
        }
        for (const control of el.querySelectorAll('button')) {
            const label = clean(control.innerText || control.getAttribute('aria-label') || '');
            const match = label.match(/\b([^\n]+\.(?:jpg|jpeg|png|gif|webp|pdf))\b/i);
            if (match && !attachments.includes(match[1])) attachments.push(match[1]);
        }
        const fileHit = text.match(/\b([\w .-]+\.(?:jpg|jpeg|png|gif|webp|pdf))\b/i);
        if (fileHit && !attachments.includes(fileHit[1])) attachments.push(fileHit[1]);
        const copy = toolbar ? toolbar.querySelector('button[aria-label="Copy"]') : null;
        if (copy) copy.dataset.domExportCopy = 'true';
        const copyRect = copy ? copy.getBoundingClientRect() : null;
        const copyReady = !!copyRect
            && copyRect.top >= rootRect.top
            && copyRect.bottom <= rootRect.bottom;
        turns.push({
            token: el.dataset.domExportToken,
            identity,
            role,
            text,
            attachments,
            top: rect.top - rootRect.top + top,
            height: rect.height,
            copy_ready: copyReady,
            markdown_features: role === 'assistant' && body ? {
                links: [...body.querySelectorAll('a[href]')].map(anchor => anchor.href),
                code_blocks: body.querySelectorAll('pre').length,
                tables: body.querySelectorAll('table').length,
                headings: body.querySelectorAll('h1, h2, h3, h4, h5, h6').length,
            } : {},
        });
    }
    turns.sort((left, right) => left.top - right.top);
    const bodyText = clean(document.body.innerText || '');
    const continuationCandidates = [];
    const hint = /(?:part\s*2|continuation|continued)/i;
    for (const anchor of document.querySelectorAll('a[href]')) {
        const context = clean((anchor.parentElement && anchor.parentElement.innerText) || anchor.innerText || '');
        const url = anchor.href || '';
        if (hint.test(context) || /(?:^|\.)cgfixit\.com(?:[/:]|$)/i.test(url)) {
            continuationCandidates.push(url);
        }
    }
    for (const match of bodyText.matchAll(/https?:\/\/[^\s)\]>"']+/gi)) {
        const start = Math.max(0, match.index - 80);
        const context = bodyText.slice(start, match.index + match[0].length + 40);
        if (hint.test(context) || /https?:\/\/(?:[^/]+\.)?cgfixit\.com(?:[/:]|$)/i.test(match[0])) {
            continuationCandidates.push(match[0]);
        }
    }
    const busy = [...document.querySelectorAll('[aria-busy="true"], [role="progressbar"]')]
        .some(el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length));
    const observedBottom = turns.reduce(
        (maximum, turn) => Math.max(maximum, turn.top + turn.height), 0
    );
    const atBottom = top + client >= height - 4;
    return {
        turns,
        metrics: {top, height, client},
        at_bottom: atBottom,
        viewport_covered: turns.length > 0 && (
            (atBottom && height <= client + 4)
            || observedBottom >= (
                Math.min(height, top + client) - Math.max(64, client * 0.15)
            )
        ),
        loading: busy,
        truncation: /Sorry,\s*could not load the rest of the thread/i.test(bodyText)
            ? 'Sorry, could not load the rest of the thread' : null,
        continuation_candidates: continuationCandidates,
        private: /This session is private|Sign in if you are the owner of this session|to request access/i.test(bodyText),
        shared: /Viewing a shared session/i.test(bodyText),
        denied: /access denied|sign in to continue/i.test(bodyText),
        challenge: /^Just a moment[.….]*$/i.test(document.title)
            || !!document.querySelector('#challenge-running, .cf-challenge, [data-testid="challenge-stage"]'),
    };
}"""


class _LivePage:
    def __init__(self, page, expected_thread_id: str | None = None, navigation_state=None):
        self.page = page
        self.expected_thread_id = expected_thread_id
        self.navigation_state = navigation_state or {"blocked": False}
        self.rate_limited = False
        page.on("response", self._observe_response)

    def _observe_response(self, response) -> None:
        try:
            parsed = urlsplit(response.url)
            host = parsed.hostname or ""
            if (host == "perplexity.ai" or host.endswith(".perplexity.ai")) and response.status == 429:
                self.rate_limited = True
        except Exception:
            pass

    def reset_top(self) -> bool:
        self.page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (el) el.scrollTop = 0;
                else window.scrollTo(0, 0);
            }""",
            SCROLL_ROOT_SELECTOR,
        )
        metrics = self.page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                return {top: el ? el.scrollTop : window.scrollY};
            }""",
            SCROLL_ROOT_SELECTOR,
        )
        return bool(metrics and metrics["top"] <= 4)

    def expand(self, pace_ms: int) -> int:
        return _expand_truncated_queries(self.page, pace_ms)

    def snapshot(self) -> dict:
        if self.expected_thread_id is not None:
            _validate_live_page_url(self.page.url, self.expected_thread_id)
        snapshot = self.page.evaluate(SNAPSHOT_SCRIPT) or {}
        snapshot["rate_limited"] = self.rate_limited
        snapshot["navigation_blocked"] = bool(self.navigation_state.get("blocked"))
        turns = snapshot.get("turns") or []
        if snapshot.get("private"):
            snapshot["access"] = "private"
        elif turns or snapshot.get("shared"):
            snapshot["access"] = "public"
        elif snapshot.get("denied"):
            snapshot["access"] = "denied"
        else:
            snapshot["access"] = "unknown"
        return snapshot

    def throttle(self, pace_ms: int) -> None:
        # Playwright actions provide readiness waits; this delay is only the request/action floor.
        self.page.wait_for_timeout(pace_ms)

    def monotonic(self) -> float:
        return time.monotonic()

    def scroll_overlap(self) -> bool:
        return bool(self.page.evaluate(
            """(sel) => {
                const el = document.querySelector(sel);
                if (el) {
                    const before = el.scrollTop;
                    const delta = Math.max(100, el.clientHeight * 0.45);
                    el.scrollTop = Math.min(el.scrollHeight - el.clientHeight, before + delta);
                    return el.scrollTop > before + 1;
                }
                const before = window.scrollY;
                const delta = Math.max(100, window.innerHeight * 0.45);
                window.scrollTo(0, Math.min(document.documentElement.scrollHeight - window.innerHeight, before + delta));
                return window.scrollY > before + 1;
            }""",
            SCROLL_ROOT_SELECTOR,
        ))

    def copy_markdown(self, turn: dict, timeout_ms: int) -> str:
        if self.expected_thread_id is not None:
            _validate_live_page_url(self.page.url, self.expected_thread_id)
        token = turn.get("token") or ""
        if not re.fullmatch(r"dom-export-\d+", token):
            raise DomExportError("Mounted answer identity changed before Copy could run.")
        host = self.page.locator(f'[data-dom-export-token="{token}"]')
        button = host.locator(COPY_BUTTON_SELECTOR)
        if host.count() != 1 or button.count() != 1:
            raise DomExportError("The answer Copy control changed before capture.")

        def mounted_turn_matches() -> bool:
            if host.count() != 1:
                return False
            observation = host.evaluate(
                """(element, rootSelector) => {
                    const clean = value => (value || '')
                        .replace(/[\u202a-\u202e\u2066-\u2069]/g, '').trim();
                    const root = document.querySelector(rootSelector);
                    const rootRect = root ? root.getBoundingClientRect() : {top: 0};
                    const top = root ? root.scrollTop : window.scrollY;
                    const direct = [...element.children];
                    const toolbar = [...direct].reverse().find(
                        child => child.querySelector('button[data-dom-export-copy="true"]')
                    );
                    const body = direct.find(child => child !== toolbar && clean(child.innerText));
                    const rect = element.getBoundingClientRect();
                    return {
                        role: element.classList.contains('group/final-text') ? 'assistant' : 'unknown',
                        text: clean(body ? body.innerText : ''),
                        top: rect.top - rootRect.top + top,
                        height: rect.height,
                    };
                }""",
                SCROLL_ROOT_SELECTOR,
            )
            tolerance = max(
                8.0,
                min(
                    float(observation.get("height") or turn.get("height") or 0),
                    float(turn.get("height") or observation.get("height") or 0),
                ) * 0.5,
            )
            return (
                observation.get("role") == "assistant"
                and clean_text(observation.get("text") or "") == clean_text(turn.get("text") or "")
                and abs(float(observation.get("top") or 0) - float(turn.get("top") or 0))
                <= tolerance
            )

        if not mounted_turn_matches():
            raise DomExportError("The mounted answer changed before Copy could run.")
        if not button.is_visible() or not button.is_enabled():
            raise DomExportError("The answer Copy control was not actionable when captured.")
        if not self.page.evaluate(
                "() => navigator.clipboard && navigator.clipboard.__domExportIsolated === true"):
            raise DomExportError("The isolated page clipboard was not installed; Copy was not clicked.")
        try:
            sequence = self.page.evaluate("() => window.__domExportClipboard.sequence")
            # The snapshot already proved the scoped control is fully visible.
            # DOM click avoids Playwright auto-scrolling a virtualized answer away.
            button.evaluate("element => element.click()")
            self.page.wait_for_function(
                """before => window.__domExportClipboard.sequence > before""",
                arg=sequence,
                timeout=timeout_ms,
                polling=100,
            )
            if not mounted_turn_matches():
                raise DomExportError("The mounted answer changed while Copy was running.")
            value = self.page.evaluate("() => window.__domExportClipboard.text")
            value = clean_text(value)
            if not value:
                raise DomExportError("Answer Copy produced an empty clipboard payload.")
            _validate_markdown_payload(
                value, turn.get("markdown_features") or {}, turn.get("text") or "",
            )
            return value
        except DomExportError:
            raise
        except Exception as exc:
            raise DomExportError(
                f"Answer Copy did not produce fresh Markdown before its deadline ({type(exc).__name__})."
            ) from exc


def _host_allowed(host: str) -> bool:
    """Exact registrable host or subdomain (avoid evilcgfixit.com false matches)."""
    host = (host or "").lower().strip(".")
    if not host:
        return False
    allowed = ("perplexity.ai", "cgfixit.com")
    return any(host == root or host.endswith("." + root) for root in allowed)


def _safe_continuation_url(value: str) -> str | None:
    value = str(value or "").rstrip(".,;")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().strip(".")
    if (
        parsed.scheme != "https"
        or not _host_allowed(host)
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
    ):
        return None
    return urlunsplit(("https", host, parsed.path, parsed.query, parsed.fragment))


def _continuation_urls(page_text: str = "", candidates=()) -> list:
    found = []
    seen = set()
    for candidate in candidates or ():
        url = _safe_continuation_url(candidate)
        if url is None:
            continue
        if url not in seen:
            seen.add(url)
            found.append(url)
    for match in URL_RE.finditer(page_text or ""):
        url = _safe_continuation_url(match.group(0))
        if url is None:
            continue
        host = urlsplit(url).hostname or ""
        # Prefer links near Part 2 / continuation wording; always keep cgfixit redirects.
        start = max(0, match.start() - 80)
        window = page_text[start: match.end() + 40]
        is_cgfixit = host == "cgfixit.com" or host.endswith(".cgfixit.com")
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


def _turn_signature(turn: dict) -> tuple:
    source_text = turn.get("_dom_text") if "_dom_text" in turn else turn.get("text")
    return (
        turn.get("role"),
        clean_text(source_text or ""),
        tuple(turn.get("attachments") or ()),
    )


def _markdown_feature_signature(turn: dict) -> tuple:
    features = turn.get("markdown_features") or turn.get("_markdown_features") or {}
    return (
        tuple(dict.fromkeys(features.get("links") or [])),
        int(features.get("code_blocks") or 0),
        int(features.get("tables") or 0),
        int(features.get("headings") or 0),
    )


def _new_record(turn: dict) -> dict:
    role = turn.get("role")
    text = clean_text(turn.get("text") or "")
    return {
        "role": role,
        "text": text,
        "attachments": list(dict.fromkeys(turn.get("attachments") or [])),
        "capture": "dom_text_fallback" if role == "assistant" else "dom_text",
        "_dom_text": text,
        "_tokens": {turn.get("token")} if turn.get("token") else set(),
        "_identity": turn.get("identity"),
        "_top": float(turn.get("top") or 0),
        "_height": float(turn.get("height") or 0),
        "_copy_attempts": 0,
        "_copy_error": None,
        "_copied_dom_text": None,
        "_copied_feature_signature": None,
        "_markdown_features": dict(turn.get("markdown_features") or {}),
    }


def _pair_support(record: dict, turn: dict) -> tuple[bool, bool]:
    if record.get("role") != turn.get("role"):
        return False, False
    token = turn.get("token")
    identity = turn.get("identity")
    same_identity = bool(identity and identity == record.get("_identity"))
    same_content = clean_text(record.get("_dom_text") or "") == clean_text(turn.get("text") or "")
    old_top = float(record.get("_top") or 0)
    new_top = float(turn.get("top") or 0)
    old_height = float(record.get("_height") or 0)
    new_height = float(turn.get("height") or 0)
    position_tolerance = max(8.0, min(old_height or new_height, new_height or old_height) * 0.5)
    same_position = abs(old_top - new_top) <= position_tolerance
    # A virtualizer may recycle one DOM element for a different turn. Its
    # collector token supports a late update only at the same logical position.
    same_node = bool(token and token in record.get("_tokens", set()) and same_position)
    same_position_content = same_content and same_position
    stable = same_identity or same_node or same_position_content
    # Attachments are monotonic metadata, not identity: they may mount later or
    # disappear before the next overlapping viewport.
    return stable, stable


def _update_record(record: dict, turn: dict) -> None:
    old_dom_text = record.get("_dom_text")
    old_feature_signature = _markdown_feature_signature(record)
    dom_text = clean_text(turn.get("text") or "")
    token = turn.get("token")
    if token:
        record.setdefault("_tokens", set()).add(token)
    if turn.get("identity"):
        record["_identity"] = turn["identity"]
    record["_top"] = float(turn.get("top") or 0)
    record["_height"] = float(turn.get("height") or 0)
    record["_dom_text"] = dom_text
    record["attachments"] = list(dict.fromkeys([
        *(record.get("attachments") or []),
        *(turn.get("attachments") or []),
    ]))
    merged_features = {"markdown_features": dict(record.get("_markdown_features") or {})}
    _merge_observation_metadata(merged_features, turn)
    record["_markdown_features"] = merged_features["markdown_features"]
    features_changed = _markdown_feature_signature(record) != old_feature_signature
    if record.get("role") == "user":
        record["text"] = dom_text
    elif dom_text != old_dom_text or features_changed:
        record["text"] = dom_text
        record["capture"] = "dom_text_fallback"
        record["_copied_dom_text"] = None
        record["_copied_feature_signature"] = None
        record["_copy_attempts"] = 0
        record["_copy_error"] = None


def _merge_batch(records: list, batch: list) -> tuple[list, str | None]:
    """Merge one settled forward viewport by contiguous suffix/prefix overlap."""
    if not batch:
        return [], "lost_overlap" if records else None
    if not records:
        pairs = []
        for turn in batch:
            record = _new_record(turn)
            records.append(record)
            pairs.append((turn, record))
        return pairs, None

    candidates = []
    content_only_candidates = 0
    for overlap in range(1, min(len(records), len(batch)) + 1):
        if all(
            record.get("role") == turn.get("role")
            and clean_text(record.get("_dom_text") or "") == clean_text(turn.get("text") or "")
            for record, turn in zip(records[-overlap:], batch[:overlap])
        ):
            content_only_candidates += 1
        support = [_pair_support(record, turn)
                   for record, turn in zip(records[-overlap:], batch[:overlap])]
        if all(valid for valid, _stable in support):
            candidates.append((overlap, sum(stable for _valid, stable in support)))
    stable_candidates = [item for item in candidates if item[1]]
    choices = stable_candidates or candidates
    if not choices:
        return [], "ambiguous_overlap" if content_only_candidates else "lost_overlap"
    if len(choices) != 1:
        return [], "ambiguous_overlap"

    overlap = choices[0][0]
    if not stable_candidates and overlap == len(batch):
        # A fully repeated remount is observationally indistinguishable from a
        # new repeated occurrence. Losing it would be worse than a partial.
        return [], "ambiguous_overlap"
    pairs = []
    for record, turn in zip(records[-overlap:], batch[:overlap]):
        _update_record(record, turn)
        pairs.append((turn, record))
    for turn in batch[overlap:]:
        record = _new_record(turn)
        records.append(record)
        pairs.append((turn, record))
    return pairs, None


def _public_turns(records: list) -> list:
    return [{
        "role": record.get("role"),
        "text": record.get("text") or "",
        "attachments": list(record.get("attachments") or []),
        "capture": record.get("capture"),
    } for record in records]


def _snapshot_fingerprint(snapshot: dict) -> tuple:
    metrics = snapshot.get("metrics") or {}
    return (
        tuple((turn.get("token"), turn.get("identity"), _turn_signature(turn),
               _markdown_feature_signature(turn), bool(turn.get("copy_ready")))
              for turn in snapshot.get("turns") or []),
        round(float(metrics.get("top") or 0), 1),
        round(float(metrics.get("height") or 0), 1),
        bool(snapshot.get("loading")),
        snapshot.get("viewport_covered"),
    )


def _turn_observation_key(turn: dict) -> tuple:
    if turn.get("identity"):
        return "identity", turn["identity"]
    if turn.get("token"):
        # DOM nodes are recyclable; token alone is not a logical-turn identity.
        return "token-position", turn["token"], round(float(turn.get("top") or 0), 1)
    return "position", turn.get("role"), round(float(turn.get("top") or 0), 1)


def _merge_observation_metadata(target: dict, source: dict) -> None:
    target["attachments"] = list(dict.fromkeys([
        *(target.get("attachments") or []),
        *(source.get("attachments") or []),
    ]))
    target_features = target.setdefault("markdown_features", {})
    source_features = source.get("markdown_features") or {}
    target_features["links"] = list(dict.fromkeys([
        *(target_features.get("links") or []),
        *(source_features.get("links") or []),
    ]))
    for name in ("code_blocks", "tables", "headings"):
        target_features[name] = max(
            int(target_features.get(name) or 0), int(source_features.get(name) or 0)
        )


def _stage_snapshot(staged: dict, turns: list) -> None:
    for turn in turns:
        key = _turn_observation_key(turn)
        if key in staged:
            attachments = list(staged[key].get("attachments") or [])
            features = dict(staged[key].get("markdown_features") or {})
            staged[key].update(turn)
            _merge_observation_metadata(staged[key], {
                "attachments": attachments, "markdown_features": features,
            })
        else:
            staged[key] = {
                **turn,
                "attachments": list(turn.get("attachments") or []),
                "markdown_features": dict(turn.get("markdown_features") or {}),
            }


def _settled_batch(staged: dict, current: list, records=()) -> tuple[list, bool]:
    batch = [{
        **turn,
        "attachments": list(turn.get("attachments") or []),
        "markdown_features": dict(turn.get("markdown_features") or {}),
    } for turn in current]
    current_keys = {_turn_observation_key(turn): index for index, turn in enumerate(batch)}
    unresolved = []
    metadata_unresolved = False
    for key, prior in staged.items():
        index = current_keys.get(key)
        if index is None:
            matches = [candidate for candidate, turn in enumerate(batch) if (
                turn.get("role") == prior.get("role")
                and clean_text(turn.get("text") or "") == clean_text(prior.get("text") or "")
                and abs(float(turn.get("top") or 0) - float(prior.get("top") or 0))
                <= max(8.0, float(turn.get("height") or 0) * 0.5)
            )]
            if len(matches) == 1:
                index = matches[0]
        if index is None:
            existing = [record for record in records if (
                record.get("role") == prior.get("role")
                and clean_text(record.get("_dom_text") or "") == clean_text(prior.get("text") or "")
                and abs(float(record.get("_top") or 0) - float(prior.get("top") or 0))
                <= max(8.0, float(prior.get("height") or 0) * 0.5)
            )]
            if len(existing) == 1:
                record = existing[0]
                old_features = _markdown_feature_signature(record)
                _update_record(record, prior)
                if _markdown_feature_signature(record) != old_features:
                    record["_copy_error"] = (
                        "Rendered Markdown structure unmounted before a fresh Copy could run."
                    )
                    metadata_unresolved = True
                continue
            missing = dict(prior)
            missing["copy_ready"] = False
            unresolved.append(missing)
        else:
            _merge_observation_metadata(batch[index], prior)
    batch.extend(unresolved)
    batch.sort(key=lambda turn: float(turn.get("top") or 0))
    return batch, bool(unresolved) or metadata_unresolved


def _turn_sequence_error(records: list) -> str | None:
    if not records or records[0].get("role") != "user":
        return "start_turn_missing"
    if any(left.get("role") == right.get("role") for left, right in zip(records, records[1:])):
        return "turn_sequence_invalid"
    if records[-1].get("role") != "assistant":
        return "unfinished"
    return None


def _checkpoint(checkpoint, records: list, report: dict) -> None:
    if checkpoint is None:
        return
    partial = dict(report)
    turns = _public_turns(records)
    partial["turns_seen"] = len(turns)
    checkpoint(turns_to_markdown(turns, partial), partial)


def _collect(adapter, report: dict, *, pace_ms: int = 1000,
             settle_timeout_ms: int = 15000, max_steps: int = 600,
             checkpoint=None) -> tuple[list, dict]:
    records = []
    report.update({
        "status": "partial",
        "stop_reason": "in_progress",
        "account_completeness": "not_verified",
        "transcript_completeness": "not_independently_verified",
        "ui_capture": "partial",
        "start_verified": bool(adapter.reset_top()),
        "steps": 0,
        "copy_failures": [],
    })
    if not report["start_verified"]:
        report["stop_reason"] = "start_not_reached"
        _checkpoint(checkpoint, records, report)
        return records, report
    now = getattr(adapter, "monotonic", time.monotonic)
    last_fingerprint = None
    quiet_dwell = (
        min(settle_timeout_ms, max(5000, pace_ms * 3)) / 1000
        if pace_ms >= 750 else max(2, pace_ms * 2) / 1000
    )
    staged = {}
    settle_deadline = now() + settle_timeout_ms / 1000
    # Process an immediate observation before the first paced wait/click so
    # transient turns, banners, and access failures cannot disappear unseen.
    pending_snapshot = adapter.snapshot() or {}
    bottom_quiet_started = None
    stop_reason = None
    changed = True

    for step in range(1, max_steps + 1):
        report["steps"] = step
        if pending_snapshot is not None:
            snapshot = pending_snapshot
            pending_snapshot = None
        else:
            try:
                adapter.expand(pace_ms)
            except DomExportError as exc:
                report["expansion_failures"] = [str(exc)]
                stop_reason = "expansion_failed"
                break
            snapshot = adapter.snapshot()
        if snapshot.get("navigation_blocked"):
            stop_reason = "navigation_blocked"
            break
        if snapshot.get("rate_limited"):
            stop_reason = "rate_limited"
            break
        if snapshot.get("challenge"):
            stop_reason = "challenge"
            break
        report["access"] = snapshot.get("access") or report.get("access") or "unknown"
        if report["access"] in {"private", "denied"}:
            if records:
                stop_reason = "access_lost"
            else:
                stop_reason = "needs_auth" if report["access"] == "private" else "access_denied"
            break
        if snapshot.get("truncation") and not report.get("ui_truncation_banner"):
            report["ui_truncation_banner"] = snapshot["truncation"]
            changed = True
        for url in _continuation_urls(candidates=snapshot.get("continuation_candidates") or []):
            if url not in report["continuation_urls"]:
                report["continuation_urls"].append(url)
                changed = True

        _stage_snapshot(staged, snapshot.get("turns") or [])

        fingerprint = _snapshot_fingerprint(snapshot)
        fingerprint_changed = fingerprint != last_fingerprint
        if fingerprint_changed:
            last_fingerprint = fingerprint
            settle_deadline = now() + settle_timeout_ms / 1000
        if (snapshot.get("loading") or snapshot.get("viewport_covered") is False
                or fingerprint_changed):
            bottom_quiet_started = None
            if now() >= settle_deadline:
                stop_reason = "settle_timeout"
                break
            adapter.throttle(pace_ms)
            continue
        if not snapshot.get("turns") and not records:
            if now() >= settle_deadline:
                stop_reason = "empty"
                break
            adapter.throttle(pace_ms)
            continue

        batch, transient_unresolved = _settled_batch(
            staged, snapshot.get("turns") or [], records,
        )
        staged = {}
        start_issue = None
        if not records and batch:
            top = float((snapshot.get("metrics") or {}).get("top") or 0)
            if top > 4:
                start_issue = "start_not_reached"
            elif batch[0].get("role") != "user":
                start_issue = "start_turn_missing"
        pairs, merge_error = _merge_batch(records, batch)
        if merge_error:
            stop_reason = merge_error
            break
        changed = bool(pairs) or changed
        retry_copy = False
        for turn, record in pairs:
            if record.get("role") != "assistant":
                continue
            feature_signature = _markdown_feature_signature(record)
            if (
                record.get("_copied_dom_text") == record.get("_dom_text")
                and record.get("_copied_feature_signature") == feature_signature
            ):
                continue
            if not turn.get("copy_ready"):
                continue
            if record.get("_copy_attempts", 0) >= 2:
                continue
            try:
                copy_turn = {**turn, "markdown_features": record.get("_markdown_features") or {}}
                record["text"] = adapter.copy_markdown(
                    copy_turn, min(10000, max(3000, pace_ms * 3)),
                )
                record["capture"] = "clipboard_markdown"
                record["_copied_dom_text"] = record.get("_dom_text")
                record["_copied_feature_signature"] = feature_signature
                record["_copy_error"] = None
                changed = True
            except DomExportError as exc:
                record["_copy_attempts"] = record.get("_copy_attempts", 0) + 1
                record["_copy_error"] = str(exc)
                retry_copy = record["_copy_attempts"] < 2
                changed = True
            adapter.throttle(pace_ms)

        report["turns_seen"] = len(records)
        if changed:
            _checkpoint(checkpoint, records, report)
            changed = False
        if retry_copy:
            continue
        if start_issue:
            stop_reason = start_issue
            break
        if transient_unresolved:
            stop_reason = "transient_turn_unresolved"
            break

        if snapshot.get("at_bottom"):
            if bottom_quiet_started is None:
                bottom_quiet_started = now()
            if now() - bottom_quiet_started >= quiet_dwell:
                report["scroll_exhausted"] = True
                break
            adapter.throttle(pace_ms)
            continue
        bottom_quiet_started = None
        if not adapter.scroll_overlap():
            stop_reason = "scroll_stalled"
            break
        last_fingerprint = None
        staged = {}
        settle_deadline = now() + settle_timeout_ms / 1000
        adapter.throttle(pace_ms)
    else:
        stop_reason = "step_limit"

    report["turns_seen"] = len(records)
    report["copy_failures"] = [
        {"turn": index + 1, "reason": record.get("_copy_error") or "Copy was not observed."}
        for index, record in enumerate(records)
        if record.get("role") == "assistant" and record.get("capture") != "clipboard_markdown"
    ]
    if stop_reason is None:
        if report.get("ui_truncation_banner"):
            stop_reason = "ui_truncated"
        elif not records:
            stop_reason = "empty"
        elif _turn_sequence_error(records):
            stop_reason = _turn_sequence_error(records)
        elif report["copy_failures"]:
            stop_reason = "copy_failed"
        elif not report.get("scroll_exhausted"):
            stop_reason = "incomplete"

    if stop_reason is None:
        report["status"] = "complete"
        report["stop_reason"] = None
        report["ui_capture"] = "exhausted"
    else:
        report["status"] = "partial"
        report["stop_reason"] = stop_reason
    _checkpoint(checkpoint, records, report)
    return records, report


def _wait_for_owner_access(page, expected_thread_id: str, seconds: int, pace_ms: int) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            on_thread = (
                urlsplit(page.url).hostname == "www.perplexity.ai"
                and exporter.resolve_share_thread_id(page.url) == expected_thread_id
            )
        except (exporter.ExportError, ValueError):
            on_thread = False
        if on_thread and not PRIVATE_RE.search(clean_text(page.inner_text("body"))):
            return True
        page.wait_for_timeout(min(1000, pace_ms))
    return False


def export_share(thread_url: str, headless: bool = True, timeout_ms: int = 60000,
                 pace_ms: int = 1000, settle_timeout_ms: int = 15000,
                 max_steps: int = 600, profile_dir: Path | None = None,
                 login_wait_seconds: int = 0, checkpoint=None) -> tuple[str, dict]:
    """Open one share, collect forward with overlap, and return Markdown/report."""
    if isinstance(pace_ms, bool) or not isinstance(pace_ms, int) or not 750 <= pace_ms <= 10000:
        raise DomExportError("DOM pacing must be between 750 and 10000 milliseconds.")
    if profile_dir is not None and headless:
        raise DomExportError("A dedicated owner profile requires --headed.")
    uid = exporter.resolve_share_thread_id(thread_url)
    # Prefer the UUID form so title-slug redirects do not change the loaded page.
    url = f"https://www.perplexity.ai/search/{uid}"
    sync_playwright = _require_playwright()
    if profile_dir is not None:
        profile_dir = _prepare_profile_dir(profile_dir)

    report = {
        "thread_uuid": uid,
        "thread_url": url,
        "access": "denied",
        "turns_seen": 0,
        "scroll_exhausted": False,
        "ui_truncation_banner": None,
        "continuation_urls": [],
        "title": "",
        "engine": "playwright-chromium",
        "status": "partial",
        "stop_reason": "in_progress",
        "account_completeness": "not_verified",
        "transcript_completeness": "not_independently_verified",
        "ui_capture": "partial",
        "auth_mode": "dedicated_profile" if profile_dir is not None else "anonymous",
        "clipboard_mode": "isolated_page_memory",
        "pacing_ms": pace_ms,
    }

    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            if profile_dir is not None:
                context = playwright.chromium.launch_persistent_context(
                    str(profile_dir), headless=False,
                    viewport={"width": 1400, "height": 900}, locale="en-US",
                    service_workers="block",
                )
            else:
                browser = playwright.chromium.launch(headless=headless)
                context = browser.new_context(
                    viewport={"width": 1400, "height": 900}, locale="en-US",
                    service_workers="block",
                )
            context.add_init_script(script=ISOLATED_CLIPBOARD_SCRIPT)
            page = context.new_page()
            navigation_guard, navigation_state = _install_navigation_guard(page, uid)
            live_page = _LivePage(
                page, expected_thread_id=uid, navigation_state=navigation_state,
            )
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            except Exception as exc:
                if navigation_state["blocked"]:
                    raise DomExportError("Blocked navigation away from the validated thread URL.") from exc
                raise
            _validate_live_page_url(page.url, uid)
            page.wait_for_function(
                "() => document.body && document.body.innerText.length > 0",
                timeout=timeout_ms,
            )
            report["title"] = clean_text(page.title())
            _dismiss_overlays(page, close_login=profile_dir is None, pace_ms=pace_ms)
            body = clean_text(page.inner_text("body"))
            report["title"] = clean_text(page.title()) or report["title"]
            if PRIVATE_RE.search(body) and profile_dir is not None and login_wait_seconds:
                page.unroute("**/*", navigation_guard)
                owner_ready = _wait_for_owner_access(
                    page, uid, login_wait_seconds, pace_ms,
                )
                if not owner_ready:
                    report["access"] = "private"
                    report["stop_reason"] = "needs_auth"
                    markdown = turns_to_markdown([], report)
                    if checkpoint:
                        checkpoint(markdown, dict(report))
                    return markdown, report
                _validate_live_page_url(page.url, uid)
                navigation_state["blocked"] = False
                page.route("**/*", navigation_guard)
                body = clean_text(page.inner_text("body"))
            if PRIVATE_RE.search(body):
                report["access"] = "private"
                report["stop_reason"] = "needs_auth"
                markdown = turns_to_markdown([], report)
                if checkpoint:
                    checkpoint(markdown, dict(report))
                return markdown, report
            records, report = _collect(
                live_page, report, pace_ms=pace_ms,
                settle_timeout_ms=settle_timeout_ms, max_steps=max_steps,
                checkpoint=checkpoint,
            )
            if not report["title"]:
                report["title"] = clean_text(page.title())
            markdown = turns_to_markdown(_public_turns(records), report)
            return markdown, report
        finally:
            if context is not None:
                context.close()
            if browser is not None:
                browser.close()


def _bounded_int(name: str, minimum: int, maximum: int):
    def parse(value):
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(f"{name} must be between {minimum} and {maximum}")
        return number
    return parse


def _partial_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.partial{path.suffix}") if path.suffix else path.with_name(path.name + ".partial")


def _atomic_final_files(files: list[tuple[Path, str]]) -> None:
    """Replace a small set of final text files, rolling back handled failures."""
    backups = {}
    replaced = []
    try:
        for path, _text in files:
            if not path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".rollback", dir=path.parent)
            os.close(fd)
            backup = Path(name)
            shutil.copyfile(path, backup)
            backups[path] = backup
        for path, text in files:
            exporter.atomic_text(path, text)
            replaced.append(path)
    except BaseException:
        for path in reversed(replaced):
            backup = backups.pop(path, None)
            if backup is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(backup, path)
        raise
    finally:
        for backup in backups.values():
            backup.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--thread-url", required=True, help="HTTPS Perplexity UUID or slug share URL")
    parser.add_argument("-o", "--output", type=Path, help="Write Markdown transcript here")
    parser.add_argument("--report", type=Path, help="Write JSON completeness report here")
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--profile-dir", type=Path,
                        help="Dedicated Chromium profile for an owner-authenticated headed run")
    parser.add_argument("--login-wait-seconds", type=_bounded_int("login wait", 0, 900), default=0,
                        help="Wait for manual owner sign-in in the dedicated headed profile")
    parser.add_argument("--pace-ms", type=_bounded_int("pace", 750, 10000), default=1000,
                        help="Minimum milliseconds between browser actions (local policy, not an official limit)")
    parser.add_argument("--settle-timeout-ms", type=_bounded_int("settle timeout", 1000, 120000),
                        default=15000, help="Bound for rendered content to reach repeated stable snapshots")
    parser.add_argument("--max-steps", type=_bounded_int("max steps", 1, 5000), default=600,
                        help="Maximum settled/scroll snapshots before an explicit partial result")
    parser.add_argument("--timeout-ms", type=_bounded_int("timeout", 1000, 600000), default=60000)
    args = parser.parse_args(argv)

    if args.profile_dir is not None and not args.headed:
        print("FAIL: --profile-dir requires --headed.", file=sys.stderr)
        return 2
    if args.login_wait_seconds and args.profile_dir is None:
        print("FAIL: --login-wait-seconds requires --profile-dir and --headed.", file=sys.stderr)
        return 2
    partial_output = _partial_path(args.output) if args.output is not None else None
    partial_report = _partial_path(args.report) if args.report is not None else None
    paths = [path.resolve() for path in (
        args.output, args.report, partial_output, partial_report
    ) if path is not None]
    if len(paths) != len(set(paths)):
        print("FAIL: output, report, and derived partial paths must be distinct.", file=sys.stderr)
        return 2
    if args.profile_dir is not None:
        profile_root = args.profile_dir.expanduser().resolve()
        if any(path == profile_root or profile_root in path.parents for path in paths):
            print("FAIL: output and report paths must be outside --profile-dir.", file=sys.stderr)
            return 2

    def checkpoint(markdown, report):
        if partial_output is not None:
            exporter.atomic_text(partial_output, markdown)
        if partial_report is not None:
            exporter.atomic_json(partial_report, report)

    try:
        markdown, report = export_share(
            args.thread_url,
            headless=not args.headed,
            timeout_ms=args.timeout_ms,
            pace_ms=args.pace_ms,
            settle_timeout_ms=args.settle_timeout_ms,
            max_steps=args.max_steps,
            profile_dir=args.profile_dir,
            login_wait_seconds=args.login_wait_seconds,
            checkpoint=checkpoint,
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
    except OSError:
        print("FAIL: Could not atomically write the DOM checkpoint.", file=sys.stderr)
        return 1
    except Exception:
        # Avoid leaking page HTML / URLs from unexpected driver errors.
        print("FAIL: DOM share export failed (browser, challenge, or UI change).", file=sys.stderr)
        return 1

    try:
        if report["status"] == "complete":
            files = []
            if args.report is not None:
                files.append((
                    args.report,
                    json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                ))
            if args.output is not None:
                files.append((args.output, markdown))
            _atomic_final_files(files)
            if args.output is None:
                sys.stdout.write(markdown)
            for path in (partial_output, partial_report):
                if path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
        else:
            checkpoint(markdown, report)
            if args.output is None:
                sys.stdout.write(markdown)
    except OSError:
        print("FAIL: Could not atomically replace the DOM export; prior final files were restored.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted; prior final files were restored.", file=sys.stderr)
        return 130

    print(
        f"DOM_EXPORT: status={report['status']} stop_reason={report['stop_reason']} "
        f"access={report['access']} turns_seen={report['turns_seen']} "
        f"scroll_exhausted={report['scroll_exhausted']} "
        f"ui_truncation_banner={bool(report['ui_truncation_banner'])} "
        f"continuation_urls={len(report['continuation_urls'])}",
        file=sys.stderr,
    )
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
