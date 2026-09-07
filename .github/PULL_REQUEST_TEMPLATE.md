## Summary

<!-- What changed and why. Keep it short. -->

## Contract impact

<!-- Does this change CLI flags, exit codes, export layout, release ZIP
     allowlist, or CI/security/resilience gates? If none, say "none". -->

## Verification

Run the standard checks from [AGENTS.md](../AGENTS.md):

```powershell
python -m unittest -v
python -m compileall -q pplx_export.py live_verify.py public_verify.py scripts tests test_export.py test_ci.py test_resilience.py
python -S -m unittest -v test_resilience tests.test_distribution
python scripts/build_release.py
python scripts/verify_release.py dist/perplexity_export.zip dist/SHA256SUMS.txt
```

<!-- Paste command results or note docs-only / N/A. -->

## Limits

- Do **not** include live cookies, session tokens, or exported transcripts.
- Do **not** commit `pplx_cookies.txt`, real `pplx_export/` output, or raw
  account JSON.
- Keep cookies and live verification local-only; see AGENTS.md and CI.md.
