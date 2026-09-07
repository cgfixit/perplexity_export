# Security Policy

## Reporting a vulnerability

If you believe you have found a security issue in `perplexity_export`, please
report it privately to the repository maintainer (GitHub user **cgfixit**) via
GitHub Security Advisories for this repository, or another private channel the
maintainer has published.

Do **not** open a public issue for vulnerabilities that could expose account
data or credentials.

## What not to include in reports

**Forbidden in any report, issue, PR, gist, or attachment:**

- Cookies, session tokens, `Authorization` headers, or other credentials
- Exported conversation transcripts or raw JSON snapshots from a real account
- Screenshots or logs that contain the above

Share reproduction steps with synthetic or redacted examples only. See
[AGENTS.md](AGENTS.md) (non-negotiable safety rules) and [CI.md](CI.md) for the
project's cookie isolation, CI boundaries, and live-verification limits.

## Supported versions

Security fixes are applied to the default branch (`main`) and to tagged
releases when practicable. Always prefer the latest release ZIP from GitHub
Releases when available.
