# perplexity_export

Export accessible Perplexity conversations to **ordinary Markdown files on the
computer running the script**. One file contains each conversation's returned
prompt/response turns. No Obsidian, Notion, or API subscription is required.

**Status:** 36 offline regression tests pass. Authenticated live account export
has not been verified. Perplexity's unofficial website endpoints can change or
omit account content; a successful crawl is not proof of a complete account backup.

## Quick start

Download or clone this repository, then open a terminal in its folder.
Requires Python 3.10 or newer.

```powershell
# Windows PowerShell
py -m pip install -r requirements.txt
```

Log into your own Perplexity account in Chrome or Edge. Copy the complete
`Cookie` request-header value from DevTools → Network into **`pplx_cookies.txt`
beside `pplx_export.py`**. Keep it private: it grants access to your session.
The [step-by-step user guide](USER_GUIDE.md) explains this and the Mac/Linux setup.

```powershell
# Check five conversations first
py .\pplx_export.py --limit 5

# Export all discovered conversations
py .\pplx_export.py

# Choose the output directory
py .\pplx_export.py -o "D:\Backups\Perplexity"
```

On Mac/Linux, use `python3` instead of `py`. A virtual-environment setup is in
the guide if your Python installation requires one.

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
py -m unittest -v
```

The standard-library suite uses simulated HTTP responses and temporary files.
It covers 125-thread discovery, 125-turn transcripts, cursor progression,
cookie isolation, partial failures, imports, and updated reruns.

Exit codes: `0` = requested scope completed without detected issues; `1` =
errors/review notes; `2` = invalid arguments; `130` = interrupted.
Always check `export_report.json` before relying on an export.

## Documentation

- [Detailed user guide](USER_GUIDE.md): installation, cookies, export, verification,
  recovery, troubleshooting, and repository publishing.
- `py pplx_export.py --help`: exact command-line options.

This is a personal utility, not an official Perplexity integration.
