# Security Policy

Third Brain is a governed knowledge layer, and its core promise is that you can never
retrieve a chunk you are not allowed to see. Anything that breaks that promise is a
security bug. So is anything that leaks credentials, crosses an organization boundary, or
lets an unauthenticated caller reach tenant data.

## Supported versions

| Version | Supported |
| --- | --- |
| `main` | Yes |
| Latest `2.x` release | Yes |
| Older `2.x` releases | No - upgrade to the latest release |
| `1.x` releases | No - superseded by 2.x |

Fixes land on `main` and ship in the next release. See [`VERSION`](../VERSION) and
[`CHANGELOG.md`](../CHANGELOG.md).

## Reporting a vulnerability

**Do not open a public issue, discussion, or pull request for a security problem.**

Report it privately through GitHub:

**<https://github.com/km322/Third-Brain/security/advisories/new>**

Please include:

- a description and an impact assessment,
- reproduction steps or a proof of concept,
- the affected version or commit,
- how to reach you for follow-up.

## What to expect

- **Acknowledgement within 7 business days.** This is a volunteer-maintained open-source
  project, so timelines are best effort, not a contractual SLA.
- **Coordinated disclosure.** Please give us a reasonable window (target **90 days**) to
  ship a fix before disclosing publicly. If the issue is being actively exploited, say so
  and we will move faster.
- **Credit.** We will credit reporters who want to be named in the advisory and the
  changelog.

## Rules for testing

Test against your own deployment. Do not access, modify, or exfiltrate data belonging to
anyone else, and do not run denial-of-service, spam, or social-engineering tests against
other people's instances. Third Brain holds real corporate knowledge; treat any data you
encounter as confidential and delete it once your report is filed.

## Full threat model

[`docs/SECURITY.md`](../docs/SECURITY.md) has the complete picture: the threat model and
trust boundaries, authentication and session revocation, the permission engine and its SQL
pushdown, secret handling and encryption at rest, secret scanning and quarantine, DLP,
tenant isolation, rate limiting, auditing, and the operator hardening checklist.
