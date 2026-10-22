# Security

Vault Cockpit is a local tool. The web server binds to `127.0.0.1` only and
checks Host, Origin and a per-session token on every write. It is not meant to
be exposed on a network, behind a reverse proxy or on a shared machine.

The cockpit reads and writes files in the vault you point it at, and can run
the lint and index commands configured in that vault's `cockpit.json`. Only
use it with vaults and configuration files you trust.

To report a vulnerability, open a private security advisory on GitHub
(Security → Report a vulnerability) instead of a public issue.
