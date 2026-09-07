# Repository guidance

## Scope

`perplexity_export` is a local Python 3.10+ utility that reads the user's own
Perplexity session through a fixed, unofficial read-only API and writes Markdown
and raw JSON locally. It is not an account-completeness guarantee or a hosted
service.

## Non-negotiable safety rules

- Never request, print, commit, upload, or place real cookies or exported
  conversations in fixtures, logs, artifacts, or pull requests.
- Keep cookies confined to the fixed HTTPS Perplexity API origin. Refuse
  redirects, unsafe URLs, malformed identifiers, and ambiguous history matches.
- Preserve atomic writes, raw received-page checkpoints, explicit pagination
  completion, and the `account_completeness: not_verified` report field.
- Do not turn malformed or partial responses into empty successful exports.
- Keep live account verification local-only; automatic CI uses fake responses.
  The manual public-thread workflow may only use anonymous public UUID access,
  independently checked expectations, and no transcript artifacts or cookies.

## Development

Use a focused `codex/*` branch for changes. Inspect `git status` before editing
and never rewrite or force-push existing remote work. The standard checks are:

```powershell
python -m unittest -v
python -m compileall -q pplx_export.py live_verify.py public_verify.py scripts tests test_export.py test_ci.py test_resilience.py
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
