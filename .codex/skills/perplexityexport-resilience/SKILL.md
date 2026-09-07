---
name: perplexityexport-resilience
description: Design and verify failure-safe changes to Perplexity export retries, checkpoints, atomic files, locks, privacy boundaries, and recovery paths.
---

# Perplexityexport Resilience

Use this skill for resilience fixes or tests in `perplexity_export`. Prefer a
small regression that proves a real failure mode over broad refactoring.

## Invariants to preserve

- Cookies go only to the fixed HTTPS `www.perplexity.ai` thread API; redirects,
  unsafe URLs, and non-JSON responses fail closed.
- A transient request can retry with bounded `Retry-After` handling, but
  permanent/authentication failures do not retry blindly.
- Repeated pages, missing cursors, malformed schemas, or incomplete advertised
  totals never become an empty or complete-success export.
- Completed raw and Markdown files survive later failures. Writes are atomic,
  and received-page checkpoints remain available for diagnosis.
- Concurrent runs are rejected by `.export.lock`; stale-lock guidance must not
  delete an unknown or non-empty directory automatically.
- Live verification is local-only. Tests use fake clients, fake responses, and
  temporary folders, never real cookies or account transcripts.

## Test failure modes

Cover the narrowest relevant path: retry exhaustion, date/number retry delays,
pagination non-progress, partial reruns, atomic replacement failure, output
path escape, lock contention and cleanup, client-close failure, artifact
tampering, and Python 3.12/3.13 subprocess behavior. Assert both the return
status/report and preservation of prior files where applicable.

Do not weaken validation merely to make a fixture pass. If an endpoint shape is
uncertain, retain the raw response and make the export require review.

## Verification commands

```powershell
python -m unittest -v test_resilience.py
python -m unittest -v
python -m compileall -q pplx_export.py live_verify.py scripts test_export.py test_ci.py test_resilience.py
python scripts/build_release.py
python scripts/verify_release.py dist/perplexity_export.zip dist/SHA256SUMS.txt
```
