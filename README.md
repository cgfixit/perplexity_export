# perplexity_export

[![CI](https://github.com/cgfixit/perplexity_export/actions/workflows/ci.yml/badge.svg)](https://github.com/cgfixit/perplexity_export/actions/workflows/ci.yml)
[![Resilience](https://github.com/cgfixit/perplexity_export/actions/workflows/resilience.yml/badge.svg)](https://github.com/cgfixit/perplexity_export/actions/workflows/resilience.yml)
[![Security](https://github.com/cgfixit/perplexity_export/actions/workflows/security.yml/badge.svg)](https://github.com/cgfixit/perplexity_export/actions/workflows/security.yml)

Export accessible Perplexity conversations to **ordinary Markdown files on the
computer running the script**. One file contains each conversation's returned
prompt/response turns. No Obsidian, Notion, or API subscription is required.

**Status:** Includes offline regression tests and cross-platform GitHub Actions. Authenticated live account export
has not been verified. Perplexity's unofficial website endpoints can change or
omit account content; a successful crawl is not proof of a complete account backup.

## Quick start

Download or clone this repository, then open a terminal in its folder.
Use a stable Python 3.12 or 3.13. Python 3.10 remains in the CI matrix;
future Python versions are not automatically verified by a “3.12+” label.

```powershell
# Windows PowerShell; use -3.13 instead for Python 3.13
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Log into your own Perplexity account in Chrome or Edge. Copy the complete
`Cookie` request-header value from DevTools → Network into **`pplx_cookies.txt`
beside `pplx_export.py`**. Keep it private: it grants access to your session.
The [step-by-step user guide](USER_GUIDE.md) explains this and the Mac/Linux setup.

```powershell
# Check five conversations first
python .\pplx_export.py --limit 5

# Export all discovered conversations
python .\pplx_export.py

# Choose the output directory
python .\pplx_export.py -o "D:\Backups\Perplexity"
```

On Mac/Linux, use `python3` instead of `py`. A virtual-environment setup is in
the guide if your Python installation requires one.

If PowerShell blocks activation, run `.\.venv\Scripts\python.exe` directly.
On macOS/Linux: `python3 -m venv .venv`, then
`source .venv/bin/activate` and `python -m pip install -r requirements.txt`.

## Output

| Path inside the output directory | Purpose |
| --- | --- |
| `markdown/chat_<full-id>.md` | A plain transcript for each conversation |
| `INDEX.md` | Clickable titles linking to those local files |
| `raw/<id>.json` | Received detail pages retained for offline rebuilding |
| `export_report.json` | Latest run status, counts, errors, and review notes |
| `records/` | Per-conversation index metadata |
| `runs/` | Run reports, discovery responses, and failed-download checkpoints |

The default output directory is `pplx_export/` **beside the script**. Use `-o`
to save somewhere else. Filenames remain stable when a title changes.

## What it preserves

- Returned prompts, response blocks, code, sources, and attachment references.
- Plan/reasoning content actually returned to the website.
- Unknown blocks as JSON with review notes, instead of silently dropping them.
- Full received detail-page JSON, including fields outside `entries`.

It does not download attachment binaries, recover deleted/expired sessions,
or expose hidden model reasoning. Some Spaces, Computer sessions, branches,
or other account content may not appear in the endpoints being queried.

## Reruns and offline rebuilding

Normal live runs refresh the listing **and every selected conversation**.
Existing complete exports remain if a later fetch fails. The script never
deletes local exports because a conversation disappears remotely.

```powershell
py .\pplx_export.py --from-raw .\pplx_export\raw -o .\rebuilt
```

Offline rebuilding requires only Python; no cookies, network, or `curl_cffi`.
It also accepts the previous exporter's REST and CLI-envelope JSON formats.

## Reliability and privacy

Detail pagination follows returned cursors. Repeated pages, malformed responses,
and incomplete pagination produce errors. Atomic writes protect individual files;
failed requests keep received pages and previous complete snapshots.

Credentials go only to the fixed Perplexity HTTPS origin. Redirects are refused;
attachment URLs are never requested. Requests are sequential and rate-limit
responses trigger bounded retries.

**Never commit cookies, exported chats, or raw snapshots, even to a private
repository.** `.gitignore` covers default output paths and common secret files.
Use an output directory outside the checkout for custom export locations.

## Tests

```powershell
python -m unittest -v
python -S -m unittest -v test_resilience tests.test_distribution
```

The standard-library suite uses simulated HTTP responses and temporary files.
It covers 125-thread discovery, 125-turn transcripts, cursor progression,
cookie isolation, partial failures, imports, and updated reruns.

`tests/test_distribution.py` extracts the allowlisted release ZIP and runs its
actual CLI in a subprocess from an unrelated directory, with site-packages
disabled. It checks Unicode and code preservation, a second rebuild, and a
failed rerun that must preserve previous Markdown. Recovery tests exercise
retry exhaustion, interruption checkpoints, disk-flush failures, and cleanup.

CI tests Python 3.10/3.12/3.13 on Linux, Windows, and macOS; the dedicated
Resilience matrix verifies the distribution and recovery paths on 3.12/3.13.
Releases require CI, Security, and Resilience to pass. These checks establish
local behavior and dependency compatibility, not live account access.

Exit codes: `0` = requested scope completed without detected issues; `1` =
errors/review notes; `2` = invalid arguments; `130` = interrupted.
Always check `export_report.json` before relying on an export.

## Documentation

- [Detailed user guide](USER_GUIDE.md): installation, cookies, export, verification,
  recovery, troubleshooting, and repository publishing.
- `py pplx_export.py --help`: exact command-line options.
- [CI and live verification](CI.md): cross-platform checks, security scans,
  release ZIPs, and a local signed-in smoke test.

Select a thread link with `--thread-url "https://www.perplexity.ai/search/..."`.
UUID links work directly; title-slug links must resolve through authenticated
account history. Shared links absent from that history require the actual UUID.
See `CI.md` for the limitations and how to verify a real browser-visible thread.

## Can CI export a shared Perplexity link?

Perplexity's **Share → Anyone with the link** setting allows viewers to open a
session ([sharing documentation](https://www.perplexity.ai/help-center/en/articles/10354769-what-is-a-thread)).
The optional **Public thread verification** Actions workflow runs a real,
anonymous export on both Python 3.12 and 3.13. It follows every returned cursor,
writes raw JSON, Markdown, records, index, and report into temporary storage,
reopens those files, rebuilds the Markdown through the offline CLI, and compares
a SHA-256 covering every canonicalized API entry. The temporary directories are
deleted and never uploaded. No cookies or repository secrets are used.

```powershell
python public_verify.py --thread-url "https://www.perplexity.ai/search/THREAD_UUID" --inspect
python public_verify.py --thread-url "https://www.perplexity.ai/search/THREAD_UUID" --expected-turns 2 --expect-text "harmless phrase" --expected-sha256 "64-lowercase-hex-characters"
```

Use a clean local `--inspect` run to obtain the structural digest, then
independently confirm the turn count and harmless phrase in the browser before
dispatching Actions. Workflow inputs remain visible in run metadata, so use only
intentionally public test content. The digest ignores only renewable signatures
on exact known Perplexity S3/CloudFront asset URLs; changed text, object paths,
hosts, ordinary URL parameters, or other fields still fail validation.

Only UUID URLs are supported anonymously; title slugs still require account
history resolution through the authenticated exporter. Availability can vary
by runner. HTTP denial, incomplete pagination, changed entries, rendering review
notes, mismatched content, disk round-trip failures, and offline-rebuild drift
fail the check. The optional live workflow is not a release gate. See
[CI.md](CI.md#shared-links-and-hosted-runners) for limits and the observed
shared-link test. Do not put account cookies in CI secrets.

This is a personal utility, not an official Perplexity integration.
