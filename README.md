# perplexity_export

[![CI](https://github.com/cgfixit/perplexity_export/actions/workflows/ci.yml/badge.svg)](https://github.com/cgfixit/perplexity_export/actions/workflows/ci.yml)
[![Resilience](https://github.com/cgfixit/perplexity_export/actions/workflows/resilience.yml/badge.svg)](https://github.com/cgfixit/perplexity_export/actions/workflows/resilience.yml)
[![Security](https://github.com/cgfixit/perplexity_export/actions/workflows/security.yml/badge.svg)](https://github.com/cgfixit/perplexity_export/actions/workflows/security.yml)

Export accessible Perplexity conversations to **ordinary Markdown files on the
computer running the script**. One file contains each conversation's returned
prompt/response turns. No Obsidian, Notion, or API subscription is required.

**Status:** Offline regression tests and cross-platform GitHub Actions (Linux, Windows, macOS) cover the
code. Authenticated live account export has **not** been verified end-to-end on large private accounts.
Perplexity's website endpoints are unofficial and can change or omit content, so a successful crawl is not
proof of a complete account backup. Live requests default to a 120 s timeout (`--timeout`, max 600).

## Pick a path

| Goal | Script | Needs |
| --- | --- | --- |
| Export **your account's** conversations to Markdown | `pplx_export.py` | `requirements.txt` + your own `pplx_cookies.txt` |
| Rebuild Markdown from saved raw JSON | `pplx_export.py --from-raw` | Python only (no cookies, network, or packages) |
| Check or export **one public shared link** (anonymous) | `public_verify.py` | `requirements.txt` |
| Browser capture of one shared/owned thread (optional) | `dom_export.py` | `requirements-dom.txt` + a Chromium download |

`requirements.txt` installs `curl_cffi`. `requirements-dom.txt` installs only
`playwright`. `requirements-ci.txt` is for CI scanners and is not needed to use the app.

## Install and first run

Requires **Python 3.10 or newer** (CI covers 3.10, 3.12, 3.13). macOS's built-in
`/usr/bin/python3` is usually 3.9 and too old; install a current Python from
[python.org](https://www.python.org/downloads/) or `brew install python@3.12`.
Always install into a virtual environment: Homebrew and recent Linux Pythons
refuse global `pip install` with *externally-managed-environment*, and a venv
also puts the `playwright` command on your PATH.

**macOS / Linux** (Terminal; bash or zsh):

```bash
git clone https://github.com/cgfixit/perplexity_export.git
cd perplexity_export
python3 -m venv .venv
source .venv/bin/activate          # re-run in every new terminal
python -m pip install -r requirements.txt
python pplx_export.py --help       # confirms the install
```

**Windows** (PowerShell; use `-3.13` for Python 3.13). If activation is blocked,
run `.\.venv\Scripts\python.exe` directly instead of activating:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python .\pplx_export.py --help
```

Log into your own Perplexity account in Chrome or Edge, open DevTools (macOS Chrome: `Cmd+Option+I`; Windows: `F12`) →
Network, reload, open a request to `www.perplexity.ai`, and copy the complete
`Cookie` request-header value into **`pplx_cookies.txt` beside `pplx_export.py`**
on one line. It is a login credential: keep it private (`chmod 600 pplx_cookies.txt`
on macOS/Linux) and never paste it into chat, CI, or a commit. More detail is in the
[user guide](USER_GUIDE.md).

```bash
python pplx_export.py --limit 5                    # try five conversations first
python pplx_export.py                              # export everything discovered
python pplx_export.py -o ~/Documents/Perplexity    # choose the output directory
python pplx_export.py --timeout 300                # longer HTTP timeout (default 120, max 600)
python pplx_export.py --thread-id THREAD_UUID      # one known conversation
python pplx_export.py --from-raw ./pplx_export/raw -o ./rebuilt   # offline rebuild
```

On Windows PowerShell the same commands work; use `.\pplx_export.py`
and `"D:\Backups\Perplexity"` style paths. Without activating the venv, call
`.venv/bin/python` (macOS/Linux) or `.\.venv\Scripts\python.exe` (Windows) directly.

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

```bash
python pplx_export.py --from-raw ./pplx_export/raw -o ./rebuilt
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

If an export was killed mid-run, verify no exporter is still running, then clear
an **empty** `.export.lock` with `python pplx_export.py -o <output> --force-unlock`
(or remove the empty directory by hand). Nonempty locks are refused.

**Never commit cookies, exported chats, or raw snapshots, even to a private
repository.** `.gitignore` covers default output paths and common secret files.
Use an output directory outside the checkout for custom export locations.

## Tests

```bash
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
- `python pplx_export.py --help` (also `dom_export.py`, `public_verify.py`, `live_verify.py`): exact command-line options.
- [CI and live verification](CI.md): cross-platform checks, security scans,
  release ZIPs, and a local signed-in smoke test.

Select a thread link with `--thread-url "https://www.perplexity.ai/search/..."`.
UUID links work directly. Public `/search/<title>-XXXX` share slugs can be decoded
to UUIDs offline (urlsafe-base64 suffix). Other title slugs still resolve through
authenticated account history. See `CI.md` for limits and DOM vs native export.

## Export or verify a public shared link

Perplexity's **Share → Anyone with the link** setting allows viewers to open a
session ([sharing documentation](https://www.perplexity.ai/help-center/en/articles/10354769-what-is-a-thread)).

### Paths compared

| Path | What it is | Fidelity | Auth |
| --- | --- | --- | --- |
| `public_verify.py --native-export` | Perplexity **Markdown API** (`POST /rest/thread/export`) | Convenient; **can omit early turns** on long shares while keeping the opener title | Anonymous for many public UUIDs |
| `public_verify.py --inspect` / structured detail | Unofficial REST detail pagination | Fail-closed on cursor loops; not a browser dump | Anonymous for some public UUIDs |
| `dom_export.py` | **DOM virtual-scroll** of the share UI (Playwright) | Ordered rendered turns plus verified answer-Copy payloads; explicit partial results on ambiguity, truncation, or Copy failure | Anonymous for public shares; dedicated local profile and manual owner sign-in for private shares |

`--native-export` is **not** a browser-faithful transcript. Use `dom_export.py`
only when UI comparison is necessary and you have independent authorization to
automate the page. [Perplexity's current Terms](https://www.perplexity.ai/en-GB/hub/legal/terms-of-service)
restrict automated extraction absent written permission or applicable law;
slower requests do not create permission. Prefer Perplexity's manual Export or
the native Markdown path when either meets the need.

### Example: anonymous export of a shared link (no login)

The short link <https://cgfixit.com/ai> redirects to this public Perplexity
thread, which is also the repository's pinned example:

```text
https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d
```

`public_verify.py` accepts only the resolved `perplexity.ai` URL (or its UUID),
not the short link, and it never reads `pplx_cookies.txt`. With the venv from
[Install](#install-and-first-run) active:

```bash
python public_verify.py \
  --thread-url "https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d" \
  --native-export --output ./example-thread.md
```

This saves Perplexity's native Markdown for that thread to `example-thread.md`
without signing in. It is the API's Markdown, not a browser-faithful capture (see
[Paths compared](#paths-compared)). To see the same short link's redirect yourself:
`curl -sI https://cgfixit.com/ai | grep -i '^location'`. The command above was
exercised only against its argument handling in this change; the live download
depends on Perplexity being reachable from your network.

### DOM share export (optional browser)

Default CI and `python -m unittest` stay green **without** Playwright. Install
only when you need share-fidelity capture. With the virtual environment from
above **activated** (any OS):

```bash
python -m pip install -r requirements-dom.txt   # installs the playwright package only
python -m playwright install chromium           # downloads the browser (~150-200 MB)
python -m playwright --version                  # confirms the CLI is present
```

Use `python -m pip` and `python -m playwright` (not bare `pip` / `playwright`):
they always target the interpreter you are running, and avoid
`command not found: playwright` on macOS when the user-scripts directory is not on
PATH. `requirements-dom.txt` is independent of `requirements.txt`; install both if
you also want the account exporter in the same environment. Re-run
`python -m playwright install chromium` after upgrading the `playwright` package,
because each release pins its own Chromium build (symptom: *Executable doesn't exist*,
reported by the tool only as "browser, challenge, or UI change").

```bash
python dom_export.py --thread-url "https://www.perplexity.ai/search/THREAD_UUID" \
  -o dom-export/share.md --report dom-export/share.json
```

PowerShell: put the command on one line and use `.\dom-export\share.md`-style paths.
Use forward slashes in bash/zsh: a backslash path such as `.\dom-export\share.md`
creates one oddly named file in the current directory on macOS/Linux.

The collector starts at a verified top, advances one overlapping viewport at a
time, and runs one browser action at a time. `--pace-ms` defaults to 1000 and is
bounded to 750–10000 milliseconds. That is a conservative local throttle, not an
official UI limit; published Perplexity API quotas do not govern browser pages or
undocumented website endpoints. A page-observed HTTP 429 stops the run rather
than adding automated retries.

For a long thread, `--settle-timeout-ms` also controls how long the bottom must
remain unchanged before collection finishes. The default is 15000 milliseconds.
New turns, layout changes, or loading indicators restart that quiet interval.
For a page that loads follow-ups slowly, allow a longer observation window:

```bash
python dom_export.py --thread-url "https://www.perplexity.ai/search/THREAD_UUID" --headed \
  --settle-timeout-ms 30000 --max-steps 1200 -o dom-export/share.md --report dom-export/share.json
```

`--max-steps` counts waiting observations as well as scroll steps. Reaching it
still produces a partial result. A longer wait can capture delayed turns, but
cannot recover history that Perplexity never loads. Compare the first user turn,
its attachments, and the final visible turns with the saved file. Export each
continuation URL as a separate conversation.

Answer Copy writes into page-local memory installed before navigation, not the
operating-system clipboard. A private thread requires an explicit, dedicated
profile; never point this at a personal browser profile:

```bash
python dom_export.py --thread-url "https://www.perplexity.ai/search/THREAD_UUID" --headed \
  --profile-dir dom-export/profile --login-wait-seconds 300 \
  -o dom-export/share.md --report dom-export/share.json
```

The JSON report distinguishes `status`, `stop_reason`, `ui_capture`,
`transcript_completeness`, and `account_completeness`. Exit 0 means the accessible
UI was traversed from its first user turn to a settled bottom with every answer
Copy verified; it does not prove the UI or account contained every historical
turn. Private/denied access, zero or unfinished turns, ambiguity, rate limiting,
Copy failure, a step/deadline cap, and the UI truncation banner exit 1. Partial
data goes to `share.partial.md` / `share.partial.json`, preserving any prior final
files. Title-slug share URLs decode to UUIDs offline. Keep profiles and exports
under ignored `dom-export/`; never commit authentication state or transcripts.

If the DOM export produces partial files, inspect `stop_reason` in the JSON
report before trying again:

| Stop reason | Next step |
| --- | --- |
| `challenge` | Open a headed run with a dedicated profile and complete the browser verification manually. A working regular Chrome tab does not share its login with the exporter. |
| `needs_auth` | Use the dedicated-profile command above and sign in as the thread owner. |
| `settle_timeout` or `step_limit` | Check whether the browser is still loading. Increase the corresponding settling or step limit only if the page can load more content. |
| `ui_truncated` | Perplexity reports that it cannot load the rest. Keep the partial files; retrying the native Markdown API does not prove that missing turns were recovered. |
| `lost_overlap`, `ambiguous_overlap`, or `copy_failed` | Keep the partial report for diagnosis. The collector could not verify turn order or answer Copy fidelity. |

The DOM browser refuses redirects and navigation to a different thread during
capture. Its navigation uses Chromium directly; it does not fetch a replacement
document through a separate HTTP client. Browser verification can still be
required, and neither successful navigation nor the original page title proves
that the first user turn was captured.

### Native Markdown API canary

The **Public thread verification** Actions workflow runs a real, anonymous
native Markdown export on both Python 3.12 and 3.13. Click **Run workflow** and
leave both fields blank to check the repository's harmless example URL against
its pinned full-file SHA-256. It also runs weekly as a non-release canary. The
file is written to temporary runner storage, reopened byte-for-byte, deleted,
and never uploaded. No cookies or repository secrets are used. A matching SHA
proves the API bytes are stable — **not** that the file equals the full UI.

```bash
python public_verify.py --thread-url "https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d" --native-export --output ./perplexity-public-export.md
```

Add the current example baseline to require an exact content match:

```bash
python public_verify.py --thread-url "https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d" --native-export --output ./perplexity-public-export.md --expected-sha256 "1b64c438d3943f6fc129430a703b7b191e0db395900746c083af66b48f5b30f9"
```

For another harmless public UUID URL, enter only that URL in the workflow. It
will verify and hash the returned Markdown without requiring a baseline.
Optionally enter a SHA-256 from a previous native-export run to detect any byte
change. Workflow inputs and the resulting byte count/digest remain visible in
run metadata and logs.

The older structured inspection and strict content checks remain available for
threads whose detail cursor reaches an explicit end:

```bash
python public_verify.py --thread-url "https://www.perplexity.ai/search/THREAD_UUID" --inspect
python public_verify.py --thread-url "https://www.perplexity.ai/search/THREAD_UUID" --expected-turns 2 --expect-text "harmless phrase" --expected-sha256 "64-lowercase-hex-characters"
```

Use a clean local `--inspect` run to obtain the structural digest, then
independently confirm the turn count and harmless phrase in the browser. The
structured digest ignores only renewable signatures on exact known Perplexity
S3/CloudFront asset URLs; changed text, object paths, hosts, ordinary URL
parameters, or other fields still fail validation.

Anonymous tooling accepts UUID URLs and decodable `/search/<title>-XXXX` share
slugs. Undecodable title slugs still need the UUID (or authenticated history
resolution in `pplx_export.py`). Availability can vary by runner. The native
workflow fails on HTTP denial, malformed, empty, oversized, or non-UTF-8
responses, digest mismatch, and disk round-trip failure. The optional live
workflow is not a release gate. See
[CI.md](CI.md#shared-links-and-hosted-runners) for limits and the observed
shared-link test. Do not put account cookies in CI secrets.

This is a personal utility, not an official Perplexity integration.
