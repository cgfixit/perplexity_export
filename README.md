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
proof of a complete account backup. Account REST requests default to a 120 s
timeout (`--timeout`, max 600).

## Which tool do I need?

Use **Python 3.10 or newer**; CI exercises 3.10, 3.12, and 3.13. Start in the
repository folder (`git clone https://github.com/cgfixit/perplexity_export.git`
then `cd perplexity_export`, or extract the source ZIP and open a terminal there).
Check `python3 --version` first; if it is older than 3.10, install a supported
Python and use that interpreter to create `.venv`.
Create a virtual environment **before** installing anything. The commands in
this table are for macOS/Linux; run each command in its cell from top to bottom.
Create the venv once and reuse it when trying another path. The example URL is
the repository's public, anonymous sample.

| What you want | Tool | Create and activate a venv | Install in that venv | Run |
| --- | --- | --- | --- | --- |
| A public share as rendered in Chromium | `dom_export.py` | `python3 -m venv .venv`<br>`source .venv/bin/activate` | `python -m pip install -r requirements-dom.txt`<br>`python -m playwright install chromium` | `python dom_export.py --thread-url "https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d" -o dom-export/share.md --report dom-export/share.json` |
| A public share's native Markdown (or REST inspection) | `public_verify.py` | `python3 -m venv .venv`<br>`source .venv/bin/activate` | `python -m pip install -r requirements.txt` | `python public_verify.py --thread-url "https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d" --native-export --output dom-export/native.md` |
| Your own or private account history | `pplx_export.py` | `python3 -m venv .venv`<br>`source .venv/bin/activate` | `python -m pip install -r requirements.txt` | Create your own `pplx_cookies.txt` as below, then `python pplx_export.py --limit 5` |
| Markdown from previously saved raw JSON | `pplx_export.py --from-raw` | `python3 -m venv .venv`<br>`source .venv/bin/activate` | None | `python pplx_export.py --from-raw ./pplx_export/raw -o ./rebuilt` |

`requirements.txt` installs `curl_cffi` for the account exporter **and** live
`public_verify.py`; the latter constructs an anonymous client and never reads
the cookie file. `requirements-dom.txt` installs Playwright only. Neither
requirements file installs the other's dependency. `requirements-ci.txt`
installs `bandit` and `pip-audit` for contributors/CI, not end users. Always
include `-r` when installing a requirements file: without it, pip searches
PyPI for a package named like `requirements-dom.txt`.

**Windows PowerShell:** from the repository folder, create and activate the
venv first. The example uses Python 3.12, one of the supported CI versions.
Then run the table's install and script commands with `python`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell activation is blocked, use `.\.venv\Scripts\python.exe` in place
of `python` in each install and run command. In a new terminal, activate the
venv again. On macOS/Linux, use `.venv/bin/python` if you do not activate it.

**Only `pplx_export.py` needs `pplx_cookies.txt` for a live account run.**
Sign in to your own account in Chrome or Edge, open DevTools → Network, reload,
and open a request to `www.perplexity.ai`. Copy only its **Cookie request-header
value** into `pplx_cookies.txt` beside `pplx_export.py`, on one line. This is a
login credential: never commit, share, or paste it into chat/CI. On macOS/Linux,
`chmod 600 pplx_cookies.txt` restricts local file access. See the
[user guide](USER_GUIDE.md) for details. DOM capture and public verification
do not use this file.

After a five-conversation account trial, run `python pplx_export.py` to export
all discovered history, or use `-o` for another output directory. `--thread-id`
selects a known UUID; `--timeout 300` raises the HTTP timeout (default 120,
maximum 600 seconds). These flags belong to `pplx_export.py`.

| Error or result | What to do |
| --- | --- |
| `externally-managed-environment` | Create and activate `.venv` first; install inside it. |
| `No matching distribution found for requirements-*.txt` | Add `-r`: use `python -m pip install -r requirements-dom.txt` or `python -m pip install -r requirements.txt`. |
| `Cannot read cookie file` | For live `pplx_export.py` only, create a readable one-line `pplx_cookies.txt` beside the script, or use `-c` with a private path. |
| 401/403 `Login expired or request blocked` | For account export, recopy your own current Cookie request-header value; a provider block can also cause 403. |
| DOM `access=denied`, `turns_seen=0` | No transcript was captured. Inspect `status` and `stop_reason` in the partial report; see [issue #15](https://github.com/cgfixit/perplexity_export/issues/15). |

The [troubleshooting guide](USER_GUIDE.md#common-problems) covers more failures.

## Account export output

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

## What the account exporter preserves

- Returned prompts, response blocks, code, sources, and attachment references.
- Plan/reasoning content actually returned to the website.
- Unknown blocks as JSON with review notes, instead of silently dropping them.
- Full received detail-page JSON, including fields outside `entries`.

It does not download attachment binaries, recover deleted/expired sessions,
or expose hidden model reasoning. Some Spaces, Computer sessions, branches,
or other account content may not appear in the endpoints being queried.

## Account reruns and offline rebuilding

Normal live runs refresh the listing **and every selected conversation**.
Existing complete exports remain if a later fetch fails. The script never
deletes local exports because a conversation disappears remotely.

```bash
python pplx_export.py --from-raw ./pplx_export/raw -o ./rebuilt
```

Offline rebuilding requires only Python; no cookies, network, or `curl_cffi`.
It also accepts the previous exporter's REST and CLI-envelope JSON formats.

## Account-export reliability and privacy

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

For **`pplx_export.py`**, exit `0` means the requested scope completed without
detected issues; `1` means errors or review notes; `2` means invalid arguments;
`130` means interruption. Always check `export_report.json` before relying on
an account export. DOM capture has a separate report and completion rule below.

## Documentation

- [Detailed user guide](USER_GUIDE.md): installation, all three live paths,
  offline rebuilding, verification, recovery, and troubleshooting.
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
not the short link, and it never reads `pplx_cookies.txt`. Install
`requirements.txt` in the activated venv from
[Which tool do I need?](#which-tool-do-i-need) before running it:

```bash
python public_verify.py \
  --thread-url "https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d" \
  --native-export --output dom-export/native.md
```

This saves Perplexity's native Markdown for that thread to `dom-export/native.md`
without signing in. It is the API's Markdown, not a browser-faithful capture (see
[Paths compared](#paths-compared)). Keep transcript output out of commits.
Availability depends on Perplexity accepting anonymous requests from your network.

### DOM share export (optional browser)

Playwright is optional and outside default CI. With the virtual environment
**activated** (any OS), install its separate requirements file and Chromium:

```bash
python -m pip install -r requirements-dom.txt   # installs the playwright package only
python -m playwright install chromium           # downloads the browser
python -m playwright --version                  # confirms the CLI is present
```

Use `python -m pip` and `python -m playwright` (not bare `pip` / `playwright`):
they always target the interpreter you are running, and avoid
`command not found: playwright` on macOS when the user-scripts directory is not on
PATH. `requirements-dom.txt` is independent of `requirements.txt`; install both if
you also want the account exporter in the same environment. Re-run
`python -m playwright install chromium` after upgrading the `playwright` package,
because each release pins its own Chromium build. A missing browser now fails
with `Chromium for this Playwright version is not installed` and the command to run.
The October 5 Linux install used about **658 MB** in `~/.cache/ms-playwright`
(size varies by version/platform; the cache is outside this repository). After
you finish using Playwright, `rm -rf ~/.cache/ms-playwright` removes that Linux
browser cache, including browsers used by other Playwright projects.

```bash
python dom_export.py --thread-url "https://www.perplexity.ai/search/THREAD_UUID" \
  -o dom-export/share.md --report dom-export/share.json
```

Without `--profile-dir`, this is a fresh anonymous browser. `--headed` only
shows its window; it does not reuse a signed-in Chrome session.

PowerShell: put the command on one line and use `.\dom-export\share.md`-style paths.
Use forward slashes in bash/zsh: a backslash path such as `.\dom-export\share.md`
creates one oddly named file in the current directory on macOS/Linux.

The collector starts at a verified top, advances one overlapping viewport at a
time, and runs one browser action at a time. If the page jumps to its last answer
while initially loading, the collector returns to the top before accepting turns.
This startup recovery is bounded by `--settle-timeout-ms` and `--max-steps`;
`start_verified` stays false until an initial settled user turn is accepted at
the top. An empty page at scroll position zero does not establish the start.
`--pace-ms` defaults to 1000 and is
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
`transcript_completeness`, and `account_completeness`. In this version, exit
0 means the accessible UI was traversed from its first user turn to a settled
bottom with every answer Copy verified; it does not prove the UI or account
contained every historical turn. Private/denied access, zero or unfinished
turns, ambiguity, rate limiting, Copy failure, a step/deadline cap, and a UI
truncation banner exit 1. Always inspect the report: the older October 5
revision could exit 0 after `access=denied` and `turns_seen=0`. Partial data
goes to `share.partial.md` / `share.partial.json`, preserving prior final files.
Title-slug share URLs decode to UUIDs offline. Keep profiles and exports under
ignored `dom-export/`; never commit authentication state or transcripts.

| DOM report field | Meaning |
| --- | --- |
| `access` | Observed page access (`public`, `private`, `denied`, or `unknown`); check it with `status`. |
| `turns_seen` | Number of collected turns; zero is not a successful transcript. |
| `scroll_exhausted` | Whether collection observed and settled at the UI bottom; it does not prove the website exposed every turn. |
| `ui_truncation_banner` | UI message saying the thread could not fully load, if seen. |
| `continuation_urls` | Separate linked threads to export as separate jobs. |
| `title` | Page title, which is not proof that the first user turn was captured. |
| `engine` | Browser engine (`playwright-chromium`). |
| `thread_uuid`, `thread_url` | Resolved UUID and canonical Perplexity URL for this thread. |

The current report has **no `source_url` key**; `thread_url` is the canonical
URL, not necessarily the original slug you supplied. Check `status`,
`stop_reason`, `start_verified`, and `turns_seen` as well as the fields above.

Perplexity's Cloudflare bot check blocked an October 5 Linux DOM run: the page
loaded, but `/rest/thread/<uuid>` returned 403 and the report had
`access=denied`, `turns_seen=0`. This limitation is recorded in
[issue #15](https://github.com/cgfixit/perplexity_export/issues/15); it is
**documented here, not solved**. Newer runs can stop for other reasons, so
inspect the actual report and compare ordered turns with the visible UI.

If the DOM export produces partial files, inspect `stop_reason` in the JSON
report before trying again:

| Stop reason | Next step |
| --- | --- |
| `challenge` | Open a headed run with a dedicated profile and complete the browser verification manually. A working regular Chrome tab does not share its login with the exporter. |
| `needs_auth` | Use the dedicated-profile command above and sign in as the thread owner. |
| `start_not_reached` | The page could not remain at the top during startup. Inspect the headed browser; no complete export was produced. |
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
