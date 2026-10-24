# Allowed runtime subset

The user expressly allowed Django while retaining the published Fancy/Prism
package policy. The initial backend uses no other independent product packages.

| Distribution | Version | Role |
|---|---|---|
| Django | 5.2.18 | Explicitly allowed framework |
| asgiref | 3.13.0 | Required Django dependency |
| sqlparse | 0.6.0 | Required Django dependency |
| tzdata | 2026.5 | Django dependency on Windows only |

`requirements.txt` contains exact wheel hashes independently checked against
primary PyPI artifacts in the envelope's package verification. A Windows isolated
install and Django consumer checks passed before application adoption. Linux
markers were inspected, but Linux execution remains open. Python standard-library
SQLite, unittest, AST/JSON/hash tooling and subprocess are platform capabilities.

The frontend will use verified published Fancy packages and its supported
dependency/tooling closure. The app's ACP transport will use published npm
`@particle-academy/prism-acp`, consumed by Maipa's own Python/Node bootstrap.
Neither is installed in this product candidate. PHP's Packagist release is
unrelated. No standalone Uvicorn, psycopg, Playwright or rejected infrastructure
proposal was adopted. No packages were patched, vendored or forked.
