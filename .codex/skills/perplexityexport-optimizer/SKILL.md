---
name: perplexityexport-optimizer
description: Find and implement evidence-backed reliability, privacy, performance, and maintainability improvements in the perplexity_export repository.
---

# Perplexityexport Optimizer

Use this skill when the user asks to optimize, harden, audit, or improve this
repository. Work from the current checkout and actual tests; do not invent
findings from file size or dependency age alone.

## Operating rules

- Inspect `git status`, `origin/main`, open workflows, and the current tests
  before editing. Keep implementation on a focused `codex/*` branch and never
  merge or push without an explicit request.
- Treat browser cookies, raw transcripts, and exported Markdown as sensitive.
  Use mocked clients and temporary directories; never request or commit live
  account data.
- Preserve the repository's safety contract: fixed HTTPS API origin, no cookie
  forwarding across redirects, atomic output replacement, retained raw
  checkpoints, explicit pagination completion, and `account_completeness` of
  `not_verified`.
- For each finding record the trigger, source evidence, user impact, smallest
  safe change, and verification. Deduplicate against existing tests and open
  work before expanding scope.

## High-value scan areas

- `pplx_export.py`: retry/backoff, pagination progress, schema validation,
  path confinement, atomic writes, lock lifecycle, and cleanup behavior.
- `test_export.py`, `test_ci.py`, and `test_resilience.py`: failure-path gaps,
  subprocess behavior, privacy assertions, and cross-platform assumptions.
- `.github/workflows/`: full-SHA action pins, least-privilege permissions,
  cancellation, matrix coverage for Python 3.12 and 3.13, artifact checks, and
  absence of live credentials.
- `scripts/build_release.py` and `scripts/verify_release.py`: deterministic
  allowlists, checksums, symlink rejection, and reproducibility.

## Verification

Run the standard-library suite under both supported modern runtimes when they
are available:

```powershell
C:\py3dot12\python.exe -m unittest -v
C:\python3dot313\python.exe -m unittest -v
```

Also run `compileall`, package build and artifact verification. Report missing
interpreters or unavailable third-party packages separately from code failures.
