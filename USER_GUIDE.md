# Perplexity Export — Detailed User Guide

For the new `--thread-url` option, signed-in smoke checks, and GitHub Actions,
see [CI and live verification](CI.md). The verifier runs locally, never with
account cookies in GitHub Actions. Being signed in in your browser alone does
not transfer that login to Python; use your own cookie file as explained below.

Run this script on your own computer. It writes **one ordinary `.md` file per
conversation**, containing the prompt/response turns returned by Perplexity.
Open them in any text editor or Markdown viewer. No Obsidian, Notion, paid API
key, or other destination account is needed.

The script includes source URLs, code, attachment references, and any plan or
reasoning blocks the website receives. Unknown response blocks are preserved as
JSON in the transcript and flagged for review. It does not download attachment
binaries, extract hidden model reasoning, or recover deleted/expired chats.

## Start on Windows

1. Install Python 3.10 or newer if needed. Extract the ZIP into a folder you own,
   such as `Documents\perplexity_export`. Open PowerShell in that folder.
2. Install the sole live-export dependency:

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. Log in to your own account at https://www.perplexity.ai in Chrome or Edge.
   Open DevTools (F12) → Network. Reload the page and open the request to
   `www.perplexity.ai`, or the `list_ask_threads` request when you open history.
   Under Request Headers, copy the **complete value** of `Cookie`.
4. Save that value on one line in **`pplx_cookies.txt` beside `pplx_export.py`**.
   Make sure your editor has not added a second `.txt` extension. This file is a
   login credential: keep it private and do not attach it to a chat or commit it.
5. Optional five-conversation smoke test:

   ```powershell
   py .\pplx_export.py --limit 5
   ```

6. Export all conversations discovered through the website's thread endpoints:

   ```powershell
   py .\pplx_export.py
   ```

If `py` is unavailable but `python` works, substitute `python` in those commands.

## Start on Mac or Linux

Use the same cookie setup, then run these commands in the extracted folder:

```bash
python3 -m pip install -r requirements.txt
python3 pplx_export.py
```

If your Python installation requires a virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python pplx_export.py
```

## Where the files go

The default output is **`pplx_export` beside the script**, on the machine running
it. It is independent of your terminal's current directory. You can choose a
different folder:

```powershell
py .\pplx_export.py -o "D:\Backups\Perplexity"
```

```bash
python3 pplx_export.py -o ~/Documents/Perplexity
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

Existing complete exports remain if a subsequent download fails. A run also
keeps its received pages on failure. Completed Markdown and raw files use atomic
replacement. Exports from conversations no longer discoverable remain locally;
this script never mirrors remote deletion into your backup.

To rebuild Markdown from saved JSON without cookies, `curl_cffi`, or network:

```powershell
py .\pplx_export.py --from-raw .\pplx_export\raw -o .\rebuilt
```

Older REST raw files and the previous exporter's `{"meta": ..., "turns": ...}`
CLI files are supported too. Keep an old `thread_index.json` or `thread_list.json`
beside its `raw` folder when available. The importer recovers missing IDs from
filenames and retains original JSON plus imported listing metadata. Older inputs
receive an explicit note because their original retrieval completeness cannot be
established retrospectively.

To check a known conversation by its UUID, independently of history discovery:

```powershell
py .\pplx_export.py --thread-id YOUR_THREAD_UUID
```

Use the thread's UUID, not its title or a whole URL. Repeat `--thread-id` for
several known conversations. A limited or targeted run is labeled as such in the
report. Do not run two exports into the same folder concurrently. If the process
was forcibly killed, verify it has stopped before removing the empty
`.export.lock` directory from the output folder and rerunning.

## How to interpret completion

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

Run the included standard-library tests (no test dependencies required):

```powershell
py -m unittest -v
```

The tests use simulated responses and temporary local files. They cover over
100 discovered threads, over 100 turns, cursor handling, repeated pages, partial
failures, cookie isolation, offline imports, stable names, and updated reruns.
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
| `py` is not recognized | Try `python --version`. Install Python 3.10+ if neither command works; reopen the terminal after installation. |
| Script file cannot be opened | Open a terminal in the extracted repository folder, or provide the full path to `pplx_export.py`. |
| Missing `curl_cffi` | Install requirements using the same interpreter you use to run the script: `py -m pip install -r requirements.txt`. |
| Externally managed Python environment | Use the Mac/Linux virtual-environment commands above; do not force installation into the system environment. |
| Cannot read cookie file | Confirm the name is exactly `pplx_cookies.txt`, not `pplx_cookies.txt.txt`, and that it is beside the script. Or provide `-c` with an explicit path. |
| Invalid cookie header | Paste only the full request `Cookie` header value on one line. Do not paste an entire request, a shell command, or a `Set-Cookie` response header. |
| HTTP 401/403 | Confirm you can view your history in the logged-in browser. Recopy the current cookies. If still blocked, stop and retry later; do not share the cookies for diagnosis. |
| HTTP 429 | Let the script honor the retry delay. If it ultimately stops, wait and retry later with `--delay 2` or a longer interval. |
| Missing cursor / schema changed | The website protocol may have changed. Received JSON is retained under the failed run. Treat that transcript as incomplete; do not change the script to ignore the error. |
| Unknown blocks / review notes | Open the affected Markdown; new block data is included as JSON. Check the raw file before deciding whether the readable export is sufficient. |
| Old imports return exit 1 | Read the notes: a legacy format cannot establish its historical pagination completeness. The imported Markdown may still be usable. |
| No chats found | Verify the browser account and the report. An empty listing is flagged for review, not treated as proof that your account has no history. |
| Disk or permission error | Choose a writable output location with sufficient free space. Existing successfully replaced files remain; rerun after fixing the local issue. |
| Another export may be running | Stop the other exporter or choose a different output directory. Remove a stale lock only after verifying the original process has ended. |

## Backup, updates, and privacy

Use an output directory outside the repository for regular exports. This reduces
the chance of accidentally staging transcripts with source-code changes.

```powershell
py .\pplx_export.py -o "$env:USERPROFILE\Documents\PerplexityHistory"
```

```bash
python3 pplx_export.py -o ~/Documents/PerplexityHistory
```

Copy the entire export folder to your usual trusted backup location. If it is
inside a cloud-synced directory, that service may upload it; choose the location
according to how you want your conversations stored. The exporter itself does
not upload transcripts or call a third-party processing service.

Before updating a Git checkout, review local changes with `git status`. If it
is clean and the remote exists, `git pull --ff-only` updates the program without
rewriting your local work. Run `py -m unittest -v` after updates. The code can
change independently of the saved raw conversations.

If a cookie was accidentally shared, use Perplexity's available account/session
controls to revoke the exposed session, then obtain fresh credentials. Removing
the text from a message or Git commit alone does not revoke a session. Never
attach a cookie file when reporting a bug. Exported raw JSON may also contain
personal text, attachment URLs, and account metadata: redact it before sharing.

## Publishing this source package to a private GitHub repository

These commands are for publishing the program and documentation, not your
conversation exports. They require Git and GitHub CLI installed locally, and a
GitHub account allowed to create `cgfixit/perplexity_export`.

From the source folder, verify GitHub CLI authentication:

```powershell
gh auth status
```

If you are not signed in, use `gh auth login` on your own machine and complete
its normal sign-in process. Confirm the selected account is `cgfixit` before
creating the repository.

If you downloaded the source ZIP and it has no Git history, initialize it and
commit only the named source/documentation files:

```powershell
git init -b main
git add pplx_export.py requirements.txt test_export.py README.md USER_GUIDE.md .gitignore
git diff --cached --stat
git status --short
git commit -m "Add local Markdown exporter, regression tests, and user guide"
```

If Git asks for author information, configure your own preferred Git author
name and email. A GitHub-provided no-reply email can keep your personal email
out of commit metadata. Do not replace an existing Git identity blindly.

Create the repository explicitly as private and push the local commit:

```powershell
gh repo create cgfixit/perplexity_export --private --source . --remote origin --push
```

If the repository already exists, inspect its visibility and contents first.
Do not force-push over existing work. For a confirmed empty private repository,
add its remote if necessary and push normally:

```powershell
git remote add origin https://github.com/cgfixit/perplexity_export.git
git push -u origin main
```

Skip `git remote add` if `origin` already points to the intended repository.
Verify the result:

```powershell
gh repo view cgfixit/perplexity_export --json nameWithOwner,isPrivate,url
git status --short
git rev-parse HEAD
git ls-remote origin refs/heads/main
```

`isPrivate` must be `true`. The local commit SHA should match the remote `main`
SHA. The tracked files should contain only source code, tests, requirements,
documentation, and `.gitignore`; cookies and exported chats do not belong there.
