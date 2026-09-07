# Contributing

Thanks for your interest in improving `perplexity_export`.

## Before you start

Read [AGENTS.md](AGENTS.md) for scope, non-negotiable safety rules, and the
development workflow. Use a focused `codex/*` or `grok/*` branch. Never put
cookies, session tokens, or exported transcripts in issues, PRs, or fixtures.

## Checks

From the repository root, with a stable Python 3.10 / 3.12 / 3.13:

```powershell
python -m unittest -v
python scripts/build_release.py
python scripts/verify_release.py dist/perplexity_export.zip dist/SHA256SUMS.txt
```

Also see the fuller compileall / `-S` resilience suite in AGENTS.md when your
change touches distribution, recovery, or CI contracts.

## Pull requests

Open a draft PR against `main` when useful. Fill in the PR template
(Summary, Contract impact, Verification, Limits). Do not force-push over
unrelated remote work.
