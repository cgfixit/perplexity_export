# CI, security, releases, and signed-in verification

## Automated checks

| Configuration | Trigger | Checks / output |
| --- | --- | --- |
| `.github/workflows/ci.yml` | Branch pushes, PRs, manual dispatch | Ubuntu, Windows, macOS × Python 3.10, 3.12, 3.13; dependency-free regression tests; actual curl_cffi installation/client construction; syntax checks; source ZIP and SHA-256 checksum |
| `.github/workflows/security.yml` | Main pushes, PRs, weekly, manual | Bandit medium/high findings and pip-audit advisories for resolved runtime and CI-tool dependencies |
| `.github/workflows/release.yml` | A pushed `v*` tag | Runs CI and security for that tag, then publishes the allowlisted source ZIP and checksum as a GitHub Release |
| `.github/dependabot.yml` | Weekly | Proposed updates to pinned GitHub Actions and pip requirements |

There is no server to deploy: continuous delivery means distributing a tested
source ZIP. No PyPI publishing, deployment credentials, or Perplexity credentials
are configured. No release is created until an authorized maintainer pushes a
version tag. This repository's current visibility controls release visibility.

Actions are pinned to full commit SHAs. Checkouts do not persist credentials.
Normal checks use `contents: read`; only the gated release-publishing job receives
`contents: write`. Workflows never use `pull_request_target`, account cookies, or
transcript artifacts. The package is assembled from an explicit source-file
allowlist, not a recursive archive of the workspace.

To require CI before merges, configure a branch ruleset in GitHub Settings and
select the resulting checks. Adding workflow files does not itself enable branch
protection. Consider a ruleset limiting who may create release tags as well.

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

```powershell
py -m unittest -v
py -m pip install -r requirements-ci.txt
py -m bandit -ll pplx_export.py live_verify.py scripts/build_release.py
py -m pip_audit --strict -r requirements.txt -r requirements-ci.txt --progress-spinner off
py scripts/build_release.py
```

Audit results reflect currently resolved dependencies and the advisory database
at run time. Runtime dependencies are not lockfile-pinned, and advisory checks
are not a proof of absence of all vulnerabilities. No package is auto-upgraded
or advisory silently suppressed by the workflow.

## Create a release after reviewing the checks

```bash
git tag v0.1.0
git push origin v0.1.0
```

Choose a new version deliberately; do not overwrite an existing tag. The release
job uses the tag's commit, reruns CI/security, builds `dist/perplexity_export.zip`
and `dist/SHA256SUMS.txt`, and publishes only after its required jobs pass.

Protocol and workflow references, checked September 7, 2026:

- https://docs.github.com/en/actions/reference/security/secure-use
- https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows
- https://pypi.org/project/pip-audit/
- https://pypi.org/project/bandit/
