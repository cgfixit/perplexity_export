# CI, security, releases, and signed-in verification

## Automated checks

| Configuration | Trigger | Checks / output |
| --- | --- | --- |
| `.github/workflows/ci.yml` | Main pushes, PRs, manual, reusable call | Ubuntu, Windows, macOS × Python 3.10, 3.12, 3.13; regression tests; actual curl_cffi installation/client construction; syntax checks; verified source ZIP and checksum |
| `.github/workflows/resilience.yml` | Main pushes, PRs, weekly, manual, reusable call | Recovery faults and shipped ZIP subprocesses without site-packages on Ubuntu, Windows, macOS × Python 3.12 and 3.13 |
| `.github/workflows/security.yml` | Main pushes, PRs, weekly, manual, reusable call | Bandit medium/high findings and pip-audit advisories for resolved runtime and CI-tool dependencies |
| `.github/workflows/release.yml` | A pushed `v*` tag | Requires CI, security, and resilience for that tag, then verifies and publishes the allowlisted source ZIP and checksum |
| `.github/dependabot.yml` | Weekly | Proposed updates to pinned GitHub Actions and pip requirements |
| `.github/workflows/public-thread.yml` | Weekly or manual | Real anonymous native Markdown export, exact-byte save/reopen, and optional complete-file digest on Python 3.12/3.13; no cookies or transcript artifacts |

There is no server to deploy: continuous delivery means distributing a tested
source ZIP. No PyPI publishing, deployment credentials, or Perplexity credentials
are configured. No release is created until an authorized maintainer pushes a
version tag. This repository's current visibility controls release visibility.

Actions are pinned to full commit SHAs. Checkouts do not persist credentials.
Normal checks use `contents: read`; only the gated release-publishing job receives
`contents: write`. Workflows never use `pull_request_target`, account cookies, or
transcript artifacts. The package is assembled from an explicit source-file
allowlist, not a recursive archive of the workspace.

**Branch protection / rulesets are not configured on `main` yet.** Adding
workflow files does not enable protection by itself. In GitHub Settings → Rules
→ Rulesets, create a ruleset for `main` and require these check names (as shown
in Actions / the merge box once the workflows have run): **CI**, **Security**,
and **Resilience**. Optionally also require **Security / Dependency review** on
PRs. Consider a separate ruleset limiting who may create release tags.


### Pending: PR dependency review job

`main` / this branch still need the following job appended to
`.github/workflows/security.yml` (PR-only so `workflow_call` from `release.yml`
is unchanged). Pin is `actions/dependency-review-action` **v5.0.0** at
`a1d282b36b6f3519aa1f3fc636f609c47dddb294`. Top-level `permissions` stay
`contents: read` only.

```yaml
  dependency-review:
    name: Dependency review
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
        with:
          persist-credentials: false
      - uses: actions/dependency-review-action@a1d282b36b6f3519aa1f3fc636f609c47dddb294 # v5.0.0
```

Apply via the GitHub web file editor on branch `grok/ci-actions-harden`, or after
`gh auth refresh -h github.com -s workflow` (Contents API rejects workflow edits
without that OAuth scope).

## What CI does and does not verify

The suite verifies transcript parsing, pagination, errors, file preservation,
URL validation, shared-slug resolution using simulated account metadata, and
the smoke-test success/failure contract. Matrix installation checks exercise the
real HTTP client's constructor without sending a Perplexity request.

**A green CI run is not an authenticated Perplexity integration test.** No live
account or shared-link compatibility claim follows from mocked HTTP responses.
Cloudflare, expired sessions, API changes, or missing history entries may prevent
a real export. No signed-in live test was performed while preparing this change.

## Safely test a real signed-in thread on your computer

1. Use a harmless test conversation in your own account. Add at least one
   follow-up. Count its prompt/response turns and choose a distinctive phrase
   visible in the conversation.
2. Save your current browser Cookie header in `pplx_cookies.txt` as described in
   `USER_GUIDE.md`. Merely being signed in in Chrome does not authenticate a
   separate Python process; the explicit cookie file provides that session.
3. Install runtime requirements and run the local verifier:

   ```powershell
   py -m pip install -r requirements.txt
   py live_verify.py --thread-url "https://www.perplexity.ai/search/YOUR_THREAD_UUID" --expected-turns 2 --expect-text "your harmless distinctive phrase"
   ```

   You can use `--thread-id YOUR_THREAD_UUID` instead. On Mac/Linux use `python3`.
4. `PASS` requires a clean one-conversation export, the exact browser-checked
   turn count, and the expected text in its Markdown. Failure exits nonzero.
   It still does not prove account-wide history coverage.
5. For manual inspection, run the exporter itself with the same selector and a
   local output directory. The verifier uses temporary files that are cleaned
   up after the check; the ordinary exporter retains its output.

The verifier refuses execution under GitHub Actions. Do not create repository
secrets containing account session cookies and do not upload private test data.
Use harmless expected text because command-line arguments may remain in your
local shell history. Nothing from a real conversation should be committed.

### Shared-link support boundaries

- HTTPS `/search/<UUID>` and `/computer/tasks/<UUID>` links target that UUID.
- A `/search/<title-slug>` link is matched to a thread UUID using the authenticated
  history/recent metadata. Exactly one match is required.
- A shared conversation belonging to someone else may be viewable in the browser
  but absent from your history. Such a slug is **not** guessed or decoded into an
  API identifier. Use the actual UUID shown in the browser's `/rest/thread/`
  network request if your session is authorized to read it.
- Short links, non-Perplexity hosts, HTTP URLs, ports, credentials in URLs,
  malformed paths, and unsupported link layouts are rejected. Input URLs are
  never fetched directly with your cookies.

Successful live verification therefore needs your real authorized session plus
a supported thread URL/ID and independently checked expected content. Share only
the pass/fail outcome when reporting results, not your cookies or transcript.

## Local developer checks

PR branches run on `pull_request`; pushes to `main` run after merge. This avoids
two copies of every matrix for each PR push. Use manual dispatch for a branch
without a PR. Pip caches are keyed by the relevant requirements files. Security
scans include all Python helpers in `scripts/`. Resilience remains a required
dependency of release publication, including on tag-triggered reusable calls.
CI also runs actionlint 1.7.12 against every workflow using a versioned download
and an exact SHA-256 digest; artifact publication depends on this check too.
Update that version and digest together after verifying the upstream release.

The archive verifier checks exact membership (including duplicates), regular
file types, checksums, and member CRCs. Its checksum detects corruption; it is
not an independent signature if someone can replace both archive and manifest.
Distribution tests run the actual packaged CLI, not a source-checkout import.

```powershell
py -m unittest -v
py -m pip install -r requirements-ci.txt
py -m bandit -ll -r pplx_export.py live_verify.py public_verify.py dom_export.py scripts
py -m pip_audit --strict -r requirements.txt -r requirements-ci.txt --progress-spinner off
py scripts/build_release.py
py scripts/verify_release.py dist/perplexity_export.zip dist/SHA256SUMS.txt
py -S -m unittest -v test_resilience tests.test_distribution
```

Audit results reflect currently resolved dependencies and the advisory database
at run time. Runtime dependencies are not lockfile-pinned, and advisory checks
are not a proof of absence of all vulnerabilities. No package is auto-upgraded
or advisory silently suppressed by the workflow.

## Shared links and hosted runners

[Perplexity's sharing documentation](https://www.perplexity.ai/help-center/en/articles/10354769-what-is-a-thread)
describes public viewing through “Anyone with the link.” It does not document
an anonymous export API contract. The regular exporter still requires account
cookies; `public_verify.py` explicitly constructs an anonymous client and only
selects the supplied UUID (or a share slug whose final segment decodes to that
UUID), never listing account history.

A credential-free check of the supplied shared page on September 7, 2026 returned
HTTP 403 to a plain client, while the anonymous thread API and in-app browser
were reachable. Repeated entries changed only renewable signatures for the same
Perplexity S3 and CloudFront objects; those narrowly defined changes are now
canonicalized for duplicate comparison and the structural digest. The API then
oscillated between an identical 50-entry page and two opaque cursors while still
claiming another page. The verifier correctly treats that as incomplete instead
of guessing that 100 entries meant completion.

Perplexity's native `/rest/thread/export` endpoint succeeds anonymously for that
same shared UUID and returns one base64-encoded Markdown file. Two clean requests
returned identical 1,090,044-byte files with SHA-256
`c5e710abce41a79780e0d010e2123f5a55707121628f1667e99cb3497f89f78f`.
The public workflow now uses this single complete-export operation instead of
the broken detail cursor for its canary. This endpoint is still unofficial and
may change.

Treat `--native-export` as **Perplexity's MD API**, not a DOM dump. On some long
shares the returned Markdown can omit the first visible user prompt while still
using that prompt as the remote filename/title. Native SHA canaries therefore
prove API-byte stability, not start→finish UI parity. For share-fidelity capture,
use optional `dom_export.py` (`pip install -r requirements-dom.txt` then
`playwright install chromium`). Default CI does **not** install Playwright or
browsers; DOM live checks are local-only. Public shares can be scrolled
anonymously; private shares need owner cookies / a signed-in browser profile and
must never place those cookies in Actions secrets.

Open Actions → Public thread verification → Run workflow and leave both fields
blank to use the example URL and pinned digest. A custom URL needs only the full
public UUID URL; its SHA-256 field is optional. Inputs are passed through
environment variables, not inserted into shell source. The Python 3.12/3.13
jobs have ten-minute timeouts, no account credentials, and no transcript
artifact upload. The temporary Markdown is reopened byte-for-byte and deleted
in an `always()` cleanup step. Inputs and digest remain visible: use harmless
public content only.

`python public_verify.py --thread-url URL --native-export --output FILE.md`
performs and saves the same native API export locally (remote `/` in titles is
allowed; `--output` is the only filesystem path used). Add
`--expected-sha256 DIGEST` for an exact baseline. `--inspect` remains a
diagnostic for the structured detail API and deliberately fails on incomplete
pagination. For UI-parity exports: `python dom_export.py --thread-url URL -o FILE.md
--report FILE.json`. Private account threads continue to use local
`live_verify.py` / `pplx_export.py` with the user's own cookies. Neither public
verification nor local checks can prove account-wide coverage.

## Create a release after reviewing the checks

```bash
git tag v0.1.0
git push origin v0.1.0
```

Choose a new version deliberately; do not overwrite an existing tag. The release
job uses the tag's commit, reruns CI/security/resilience, builds `dist/perplexity_export.zip`
and `dist/SHA256SUMS.txt`, and publishes only after its required jobs pass.

Protocol and workflow references, checked September 7, 2026:

- https://docs.github.com/en/actions/reference/security/secure-use
- https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows
- https://pypi.org/project/pip-audit/
- https://pypi.org/project/bandit/
