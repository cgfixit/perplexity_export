# Repository guidance

## Scope

`perplexity_export` is a local Python 3.10+ utility with three export paths:
authenticated account REST (`pplx_export.py`), anonymous REST/native Markdown
verification (`public_verify.py`), and optional browser DOM capture
(`dom_export.py`). It writes local files; it is not an account-completeness
guarantee or a hosted service. Native Markdown bytes are not proof of full UI
capture. The DOM implementation still has the gaps recorded below.

## Non-negotiable safety rules

- Never request, print, commit, upload, or place real cookies or exported
  conversations in fixtures, logs, artifacts, or pull requests.
- Keep cookies confined to the fixed HTTPS Perplexity API origin. Refuse
  redirects, unsafe URLs, malformed identifiers, and ambiguous history matches.
- Preserve atomic writes, raw received-page checkpoints, explicit pagination
  completion, and the `account_completeness: not_verified` report field.
- Do not turn malformed or partial responses into empty successful exports.
- Keep live account verification local-only; automatic release CI uses fake
  responses. The isolated public-thread canary may use only anonymous public
  UUID access, Perplexity's native Markdown export (incomplete for some long
  shares), a pinned harmless baseline, temporary local artifacts that are
  deleted, and no transcript uploads or cookies. Share-fidelity DOM export is
  optional (`requirements-dom.txt`) and must remain outside default CI. The
  canary must remain outside release and merge gates.

## Development

Use a focused `codex/*` or `grok/*` branch for changes. Inspect `git status` before editing
and never rewrite or force-push existing remote work. The standard checks are:

```powershell
python -m unittest -v
python -m compileall -q pplx_export.py live_verify.py public_verify.py dom_export.py scripts tests test_export.py test_ci.py test_resilience.py
python -S -m unittest -v test_resilience tests.test_distribution
python scripts/build_release.py
python scripts/verify_release.py dist/perplexity_export.zip dist/SHA256SUMS.txt
```

The CI contract covers Python 3.10, 3.12, and 3.13 on supported operating
systems, with shipped-archive and resilience coverage for Python 3.12 and 3.13.
Use stable interpreters and record exact versions when making compatibility
claims. Discover their locations on the current host; an available Python 3.12
prerelease is not a substitute for stable 3.12 verification.

## Release boundary

`scripts/build_release.py` is intentionally allowlisted. If a source file is
needed by the distributable, add it deliberately and update its package tests.
Release publication is tag-driven and requires CI/security/resilience success. Do not
create tags or publish artifacts without explicit authorization.

## Repository skills

The repo-local `.codex/skills/` directory contains focused workflows:

- `perplexityexport-optimizer` for evidence-backed improvements.
- `perplexityexport-resilience` for failure-mode and recovery work.
- `perplexityexport-release` for compatibility, CI/CD, and artifact gates.

## Issue #15: paced virtual-DOM capture to Markdown

Reviewed 2026-09-11 against `origin/main` at
`dcfeed99aa6ae14ad188c8a501852156ce1c25ee` and
[issue #15 including its comments](https://github.com/cgfixit/perplexity_export/issues/15).
Recheck code and issue updates before implementation. The issue's original
claim that no DOM exporter exists is stale: `b468e5d` added it. Extend that
implementation rather than creating a second exporter.

### Observed gaps, not completed features

- `export_share()` captures mounted nodes during a second scroll pass, but
  `ingest()` identifies turns by role plus only the first 240 text characters.
  Distinct long prompts and repeated short prompts can disappear. A synthetic
  call through `export_share()` reproduced two distinct prompts becoming one.
- The first scroll pass determines `scroll_exhausted`; the collecting pass has
  a separate fixed 60-step (30 for document scrolling) cap and 280 ms wait.
  Reaching that cap does not establish that every mounted turn was captured.
  Slower waits alone cannot repair identity or completion errors.
- `_extract_turns()` reads `innerText`, not clipboard Markdown. Link targets,
  heading levels, code fences, and table structure are not preserved by that
  operation. There is no per-answer Copy-button/clipboard implementation.
- Truncation and continuation detection use only the final body text. Earlier
  banners/links can unmount; linked labels may omit the actual `href` entirely.
- `browser.new_context()` starts a fresh anonymous session. `--headed` only
  shows the window: it does not reuse Chrome login, load cookies, or pause for
  owner sign-in. The issue's browser audit reported Part 2 as private; its
  current visibility was not independently checked in this review.
- `main()` returns zero for private, empty, unfinished, and UI-truncated reports;
  synthetic CLI checks confirmed this. Direct `Path.write_text()` also bypasses
  the existing `pplx_export.atomic_text()` / `atomic_json()` write helpers.

### Next implementation steps

1. Inspect a harmless live thread first: identify the actual scroll container,
   stable turn identifiers, prompt expansion, and each answer's Copy control.
   Verify what that control puts on the clipboard (Markdown versus plain text)
   using links, a code block, and a table. Do not invent selectors or assume
   session-level Export as Markdown captures the same content.
2. Use one sequential collection loop from a verified top position: mount a
   turn, expand it, wait for content to settle, capture its prompt/attachments,
   click that answer's verified Copy control, confirm the corresponding fresh
   clipboard payload, save it locally, then scroll with viewport overlap.
   Make pacing adjustable with bounded readiness/retry deadlines; capture
   before nodes unmount. Do not copy the whole page after scrolling to the end.
   Treat unavailable Markdown copy as an explicit failure or labeled text
   fallback, never silently claim Markdown fidelity. Keep clipboard operations
   isolated where possible; never log or overwrite unrelated clipboard content
   without an explicit operator choice. Repeated identical answers must remain
   valid: clipboard text changing is not sufficient evidence of a fresh copy.
3. Deduplicate by observed stable turn identity, preserve conversation order,
   and update an existing turn when its content finishes loading. Never use a
   text prefix or text equality alone as identity. If the live UI exposes no
   usable IDs, prove overlap-based ordering on a fixture before relying on it.
4. Accumulate attachment metadata, truncation banners, and continuation `href`s
   at every collection step. Record Part 2 as a separate conversation/job.
   Private capture requires a deliberate local owner-authentication path (for
   example a dedicated headed profile with manual login); never ask for a
   cookie/token in chat or send it to CI. Do not silently reuse a personal profile.
5. Derive completion from the collecting pass: verified start, observed bottom,
   settled content, and no unresolved loading/copy failures. Distinguish UI
   exhaustion from transcript completeness. Cap exhaustion, private/denied
   access, zero turns, and truncation must return nonzero with a reason. Save
   recoverable partial data separately and preserve a previous complete export;
   reuse the existing atomic write helpers for checkpoints and final files.

### Scope and acceptance for the follow-up fix

Start with `dom_export.py` and `tests/test_dom_export.py`; update README and
USER_GUIDE only for options actually implemented. Reuse optional Playwright
and standard-library helpers. Keep REST export behavior and release gates intact.

- Add an offline synthetic virtualized-page check that unmounts old turns and
  exercises delayed rendering, repeated prompts, matching 240-character
  prefixes, order, clipboard failure, and stop-limit exhaustion. Test real
  collection behavior, not only `turns_to_markdown()`.
- Check that banners and continuation links survive unmounting, Markdown copy
  retains links/code/tables, and an interrupted write preserves the prior file.
- Keep default tests browser-free; run an optional local browser fixture to
  verify actual scrolling, selector behavior, and clipboard interaction.
- Live Part 1 acceptance requires the first **user turn** to contain the
  Liberation Day/image opener and its attachment metadata. A phrase in the
  document title does not count. Compare ordered turns with the visible UI;
  report its loading failure if still present. Part 2 requires a separate,
  owner-authorized capture. Neither one proves account-wide completeness.
- Run the standard checks above and report live checks separately. This review
  ran the baseline on stable Python 3.12.14: 105 tests, 104 passed, one live DOM
  test skipped. No live clipboard capture or private transcript export was run.
