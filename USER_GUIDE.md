# Perplexity Export — Detailed User Guide

This local utility has three live paths: `pplx_export.py` for your own account
history, `public_verify.py` for an anonymous public share's native Markdown or
REST inspection, and `dom_export.py` for browser capture of a shared thread.
Only live `pplx_export.py` reads `pplx_cookies.txt`. Export files stay on the
computer running the scripts; never commit credentials or conversation files.
See [CI and live verification](CI.md) for what hosted checks do and do not prove.

The account exporter writes one Markdown file per returned conversation and
retains raw JSON for later rebuilding. It can include source URLs, code, and
attachment references, but it does not download attachment binaries, recover
deleted chats, or establish account-wide completeness. Native Markdown from a
public share can omit UI-visible turns on long threads. DOM capture may also
end with a partial result; check its report and compare with the visible UI.

## Step 1: create a virtual environment

Use **Python 3.10 or newer**; CI tests 3.10, 3.12, and 3.13. Start in the cloned
repository or extracted source ZIP folder, where the scripts and requirements
files are present. Check your Python version before creating the environment.
Do not install into a system Python: PEP 668 environments reject global pip
installs with `externally-managed-environment`.

**macOS/Linux (bash or zsh):**

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
```

If `python3` is older than 3.10, install a supported Python and use its command
to create the venv. **Windows PowerShell** (3.12 is a supported example):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Activate `.venv` again in each new terminal. If PowerShell blocks activation,
use `.\.venv\Scripts\python.exe` instead of `python` in every command below.
The Linux/macOS equivalent is `.venv/bin/python`. All install commands use
`python -m pip install -r <file>`: `-r` tells pip to read the file.

| Requirements file | What it installs | Who needs it |
| --- | --- | --- |
| `requirements.txt` | `curl_cffi` | Live `pplx_export.py` and live anonymous `public_verify.py` |
| `requirements-dom.txt` | `playwright` | `dom_export.py`; install Chromium separately |
| `requirements-ci.txt` | `bandit`, `pip-audit` | Contributors and CI only |
| None | Standard library only | `pplx_export.py --from-raw` |

## Step 2A: export your own account history

With the venv active, install the REST dependency:

```bash
python -m pip install -r requirements.txt
```

Sign in to your own account at <https://www.perplexity.ai> in Chrome or Edge.
Open DevTools → Network, reload the site, and open a signed-in request to
`www.perplexity.ai` (for example `list_ask_threads` in history). Copy the
**Cookie request-header value**, not the `Set-Cookie` response header or an
entire request. Put that value on **one line** in `pplx_cookies.txt` beside
`pplx_export.py`. On Windows, check that the file is not accidentally named
`pplx_cookies.txt.txt`. Never commit, upload, or share this credential; on
macOS/Linux, run `chmod 600 pplx_cookies.txt` to limit local access.

```bash
python pplx_export.py --limit 5
python pplx_export.py
```

The first command samples five conversations; the second exports all discovered
history. The default output is `pplx_export/` beside the script. To select one
known UUID, use `python pplx_export.py --thread-id YOUR_THREAD_UUID`. To use a
different cookie-file path, use `-c`, but keep that path outside version control.
Being signed in to a normal browser tab does not transfer its login to this
script. `pplx_export.py` needs the separate one-line cookie file for live runs.

## Step 2B: export or inspect a public share without cookies

`public_verify.py` uses the same `curl_cffi` dependency but creates an anonymous
client. It never reads `pplx_cookies.txt`. With the venv active:

```bash
python -m pip install -r requirements.txt
python public_verify.py --thread-url "https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d" --native-export --output dom-export/native.md
```

The `--output` file contains Perplexity's native Markdown bytes. This can omit
earlier turns from a long public share, even when its title looks correct. For
structured REST counts without retaining a transcript, replace the second
command with `python public_verify.py --thread-url "https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d" --inspect`.
Use a direct `perplexity.ai` UUID or decodable share URL, not a short redirect.
Anonymous access can be blocked by the provider.

## Step 2C: capture a shared thread in Chromium

Use this path to compare rendered turns with a public share's visible UI. With
the venv active, install Playwright and its Chromium build, then run the script:

```bash
python -m pip install -r requirements-dom.txt
python -m playwright install chromium
python dom_export.py --thread-url "https://www.perplexity.ai/search/65f1c6ad-8600-4393-aec2-0a4f7d8a1e8d" -o dom-export/share.md --report dom-export/share.json
```

`python -m playwright install chromium` downloaded about **658 MB** into
`~/.cache/ms-playwright` on the verified October 5 Linux run; the size varies
by version and OS and the cache is outside the repository. On Linux,
`rm -rf ~/.cache/ms-playwright` removes that cache when you no longer need it,
including browsers shared by other Playwright projects. Reinstall Chromium
after upgrading the Playwright package, which pins its browser build.

Without `--profile-dir`, `dom_export.py` creates a fresh anonymous browser
context. `--headed` only shows that window; it does not reuse your Chrome login.
The current CLI also supports a **separate owner-authenticated mode** for a
private thread: use a new dedicated profile and sign in manually in the headed
window. Never point it at a personal browser profile or put authentication
state in CI:

```bash
python dom_export.py --thread-url "https://www.perplexity.ai/search/YOUR_THREAD_UUID" --headed --profile-dir dom-export/profile --login-wait-seconds 300 -o dom-export/share.md --report dom-export/share.json
```

The collector advances sequentially with a local default pace of one action
per second (`--pace-ms 1000`, allowed range 750–10000). It keeps answer Copy
payloads in isolated page memory, rather than your operating-system clipboard.
It does not guarantee that Perplexity exposed every historical turn. Export
each `continuation_urls` entry as a separate thread.

Read the JSON report after every DOM run. `access` identifies public/private/
denied/unknown access; `turns_seen` counts collected turns;
`scroll_exhausted` says whether the collector settled at the UI bottom;
`ui_truncation_banner` records a visible load failure; and
`continuation_urls` lists linked threads. `title` is the page title, `engine`
identifies `playwright-chromium`, and `thread_uuid`/`thread_url` identify the
resolved thread. This version does **not** emit `source_url`: `thread_url` is
the canonical URL, which can differ from a supplied slug. Also inspect
`status`, `stop_reason`, and `start_verified`; a title or bottom position alone
does not establish a complete transcript.

In the current CLI, exit 0 requires a complete traversal of the accessible UI;
denied access, zero or unfinished turns, a challenge, Copy failure, truncation,
or a step limit exits 1. Partial data is written as `share.partial.md` and
`share.partial.json` without replacing previous final files. The older October
5 build could exit 0 for `access=denied`, `turns_seen=0`; always check the
report even when exit status is 0. That Linux run loaded the page but received
Cloudflare HTTP 403 on `/rest/thread/<uuid>`. See
[issue #15](https://github.com/cgfixit/perplexity_export/issues/15). This
provider limitation is documented, not solved; current runs can fail for
other reasons too. Do not interpret native Markdown or a page title as proof of
full UI capture.

## Where the account files go

For `pplx_export.py`, the default output is **`pplx_export` beside the script**,
on the machine running it. It is independent of your terminal's current
directory. You can choose a different folder:

```powershell
python .\pplx_export.py -o "D:\Backups\Perplexity"
```

```bash
python pplx_export.py -o ~/Documents/Perplexity
```

Output files:

| Path | Contents |
| --- | --- |
| `markdown/chat_<full-id>.md` | One transcript with all returned turns and response blocks |
| `INDEX.md` | Local links labeled with conversation titles |
| `raw/<id>.json` | Complete received detail pages, including fields from later pages |
| `export_report.json` | Latest run counts, errors, review notes, and selected scope |
| `records/` | Per-conversation index metadata |
| `runs/` | Listing responses, run reports, and received pages retained after failed downloads |

Transcript filenames use the full identity, so title changes and shared ID
prefixes do not create duplicate or colliding files. The readable title appears
inside each transcript and in `INDEX.md`. Dates use server-provided timestamps;
no operating-system timezone database is required.

## Reruns and recovery

**Normal live reruns always refresh the listing and every selected conversation.**
There is no stale-cache skip and no need for `--refresh` or `--redo`. A rerun
captures added messages and repairs missing Markdown. It can take time for large
accounts: requests are sequential, with a one-second minimum interval by default.
Use `--delay 2` to slow down. Rate-limit responses trigger bounded retries.
HTTP requests default to a **120-second** timeout (was previously hardcoded at 60s) so longer detail pages have more room; raise it with `--timeout 300` (max 600) if a large thread still times out.

Existing complete exports remain if a subsequent download fails. A run also
keeps its received pages on failure. Completed Markdown and raw files use atomic
replacement. Exports from conversations no longer discoverable remain locally;
this script never mirrors remote deletion into your backup.

To rebuild Markdown from saved JSON without cookies, `curl_cffi`, or network:

```powershell
python .\pplx_export.py --from-raw .\pplx_export\raw -o .\rebuilt
```

Older REST raw files and the previous exporter's `{"meta": ..., "turns": ...}`
CLI files are supported too. Keep an old `thread_index.json` or `thread_list.json`
beside its `raw` folder when available. The importer recovers missing IDs from
filenames and retains original JSON plus imported listing metadata. Older inputs
receive an explicit note because their original retrieval completeness cannot be
established retrospectively.

To check a known conversation by its UUID, independently of history discovery:

```powershell
python .\pplx_export.py --thread-id YOUR_THREAD_UUID
```

Use the thread's UUID, not its title or a whole URL. Repeat `--thread-id` for
several known conversations. A limited or targeted run is labeled as such in the
report. Do not run two exports into the same folder concurrently. If the process
was forcibly killed, verify it has stopped before removing the empty
`.export.lock` directory from the output folder and rerunning. Prefer
`python .\pplx_export.py -o <output> --force-unlock`, which removes only an empty
lock directory and refuses nonempty ones.

## How to interpret account-export completion

- Exit **0**: the requested scope finished without detected errors or rendering
  notes. This is **not proof of account-wide completeness**.
- Exit **1**: an export failed or needs review. Successful transcripts remain;
  read `export_report.json` and the notes in affected Markdown.
- Exit **2**: invalid command-line arguments. Exit **130**: interrupted by you.
- A 401/403 means expired login or a blocked request. Recopy your own cookies.
  Browser impersonation is not a guarantee of access. Redirects are stopped;
  cookies are never forwarded to redirected or attachment destinations.

This uses **unofficial website endpoints**, not a supported account-backup API.
It pages the history list until an explicit end or empty page, supplements that
with the recent list, and follows returned detail cursors. It detects repeated
pages/cursors, schema changes, and advertised count mismatches. It does not guess
a next detail cursor or call a partial response a complete conversation.

The website may exclude some Spaces, Computer activity, alternate branches,
deleted/expired sessions, or other data. No script can infer what an endpoint
never exposes. Compare the result against a known old conversation, a thread
with over 50 turns, and any Spaces/Computer sessions you care about before
treating it as your only archive. The report always marks account completeness
as unverified. New response schemas are reported, not silently treated as empty.

## Validation and provenance

Python 3.12 and 3.13 are tested on Linux, Windows, and macOS. Use a stable
interpreter; CI also retains Python 3.10 coverage. Tests now execute the actual
release ZIP from a separate directory, preserving Unicode through repeated
offline rebuilds and checking that failed reruns retain prior transcripts.
See [CI.md](CI.md) for the distinction between these checks and real account
verification, including why a publicly shared link alone does not authenticate
this exporter.

GitHub Actions runs standard-library tests with simulated responses and
temporary files. They cover discovery, long transcripts, cursor handling,
partial failures, cookie isolation, and offline imports. These checks do not
establish that a particular live account or share is fully exportable.
**No authenticated live account export was tested during preparation.**

The implementation was independently written for this task using the supplied
scripts as requirements and the following protocol/library references, checked
September 7, 2026. Public code corroborates endpoint shapes; it is not an official
contract or evidence of compatibility with your account.

- [AfterChat source: detail next_cursor/from_first and supplemental recent listing](https://greasyfork.org/en/scripts/589622-afterchat-llm-chat-exporter/code)
- [pxcli documentation: list_ask_threads endpoint](https://pypi.org/project/pxcli/)
- [curl_cffi documentation](https://curl-cffi.readthedocs.io/en/latest/quick_start.html)

## Verify your first export

1. Open the output folder printed in the terminal. Open `INDEX.md` in your editor
   or Markdown viewer and select a conversation title.
2. Compare its first and last prompts with the same conversation on Perplexity.
   Check that code fences, source URLs, and any follow-up turns are present.
3. Inspect `export_report.json` in a text editor. `exported` counts transcripts
   successfully written in this run; `selected` counts the requested conversations.
   These are not necessarily the total number of files retained from earlier runs.
4. Read every item under `errors` and `warnings`. `completed_requested_scope`
   means no detected error within this run's selected scope. `account_completeness`
   intentionally remains `not_verified`.
5. After a five-thread test, run again without `--limit` to attempt the full list.
   Check an old conversation, a conversation longer than 50 turns, and any special
   session types you use. A five-thread test cannot validate the entire account.
6. Keep both the Markdown and the raw JSON. Raw files allow improved rendering
   later without logging in again. The raw files contain the conversations too,
   so protect them as carefully as the readable transcripts.

For a quick report in PowerShell:

```powershell
$report = Get-Content .\pplx_export\export_report.json -Raw | ConvertFrom-Json
$report | Select-Object status, selected, exported, account_completeness
$report.errors
$report.warnings
```

For an exit-code check, run this immediately after the exporter:

```powershell
$LASTEXITCODE
```

On Mac/Linux, use `echo $?` immediately after the exporter.

## Common problems

| Symptom | What to check or do |
| --- | --- |
| `py` is not recognized | On Windows, install a supported Python and reopen PowerShell; use its launcher to create `.venv`. After activation, run scripts with `python`. |
| Script file cannot be opened | Open a terminal in the extracted repository folder, or provide the full path to `pplx_export.py`. |
| Missing `curl_cffi` | In the activated venv, run `python -m pip install -r requirements.txt` for `pplx_export.py` or `public_verify.py`. |
| `externally-managed-environment` | You ran pip against system Python. Run Step 1, activate `.venv`, then use `python -m pip install -r requirements.txt` for REST or `python -m pip install -r requirements-dom.txt` for DOM. Do not force a system install. |
| `No matching distribution found for requirements-*.txt` | You omitted `-r`. In the activated venv, use `python -m pip install -r requirements-dom.txt` for DOM or `python -m pip install -r requirements.txt` for REST; pip otherwise searches for a PyPI package with that filename. |
| `Cannot read cookie file` | This applies to live `pplx_export.py` only. Confirm `pplx_cookies.txt` is beside the script, is readable, and has no extra `.txt` extension; or use `-c` with your private path. |
| Invalid cookie header | Paste only the full request `Cookie` header value on one line. Do not paste an entire request, a shell command, or a `Set-Cookie` response header. |
| 401/403 `Login expired or request blocked` | For account export, confirm you can view your history in the signed-in browser and recopy your own one-line Cookie request-header value. A 403 can also mean a provider block; do not share cookies for diagnosis. Anonymous `public_verify.py` can also be denied, but has no cookie file to refresh. |
| DOM `access=denied`, `turns_seen=0` | No transcript was captured. Check `status` and `stop_reason` in `share.partial.json`; a page loading is not evidence of access. An October 5 Linux run received Cloudflare 403 on `/rest/thread/<uuid>`. See [issue #15](https://github.com/cgfixit/perplexity_export/issues/15); this is documented, not fixed. |
| Chromium for this Playwright version is not installed | In the activated venv, run `python -m playwright install chromium`; repeat after Playwright upgrades. |
| HTTP 429 | Let the script honor the retry delay. If it ultimately stops, wait and retry later with `--delay 2` or a longer interval. |
| Network request failed / timeout on a long thread | Detail pages for ~100+ turns can be slow. For `pplx_export.py`, retry with `python pplx_export.py --timeout 300 --thread-id YOUR_THREAD_UUID` (default 120; max 600 seconds). |
| Missing cursor / schema changed | The website protocol may have changed. Received JSON is retained under the failed run. Treat that transcript as incomplete; do not change the script to ignore the error. |
| Unknown blocks / review notes | Open the affected Markdown; new block data is included as JSON. Check the raw file before deciding whether the readable export is sufficient. |
| Old imports return exit 1 | Read the notes: a legacy format cannot establish its historical pagination completeness. The imported Markdown may still be usable. |
| No chats found | Verify the browser account and the report. An empty listing is flagged for review, not treated as proof that your account has no history. |
| Disk or permission error | Choose a writable output location with sufficient free space. Existing successfully replaced files remain; rerun after fixing the local issue. |
| Another export may be running | Stop the other exporter or choose a different output directory. After confirming the process ended, clear an empty lock with `--force-unlock` (nonempty locks are refused). |

## Backup, updates, and privacy

Use an output directory outside the repository for regular exports. This reduces
the chance of accidentally staging transcripts with source-code changes.

```powershell
python .\pplx_export.py -o "$env:USERPROFILE\Documents\PerplexityHistory"
```

```bash
python pplx_export.py -o ~/Documents/PerplexityHistory
```

Copy the entire export folder to your usual trusted backup location. If it is
inside a cloud-synced directory, that service may upload it; choose the location
according to how you want your conversations stored. The exporter itself does
not upload transcripts or call a third-party processing service.

Before updating a Git checkout, review local changes with `git status`. If it
is clean and the remote exists, `git pull --ff-only` updates the program without
rewriting your local work. Activate the venv again and reinstall only the
requirements files for your chosen path after updates. The code can change
independently of saved raw conversations.

If a cookie was accidentally shared, use Perplexity's available account/session
controls to revoke the exposed session, then obtain fresh credentials. Removing
the text from a message or Git commit alone does not revoke a session. Never
attach a cookie file when reporting a bug. Exported raw JSON may also contain
personal text, attachment URLs, and account metadata: redact it before sharing.
