---
name: vault-cockpit
description: Work through the vault cockpit inbox (answers and decision drafts a human entered in the cockpit), record new open questions as items, and propose triage verdicts. Use when the user says "work through the cockpit inbox", "Cockpit-Inbox abarbeiten", or when an ingest produces open questions in a vault that has a cockpit.json.
---

# Vault Cockpit

The vault keeps open questions as one file per item and decisions in a sealed
register. The human answers and decides in the cockpit (`vault-cockpit serve`);
you work the answers into the wiki. Format: `spec/FORMAT.md` in the
vault-cockpit repo. The vault's own schema (e.g. `CLAUDE.md`) takes precedence.

Run every command from the vault root, or pass `--vault <path>`. If `vault-cockpit` is not on the PATH, use the call the vault's schema names
(for example a wrapper script) or `python3 -m vault_cockpit`.

## 1. Work through the inbox

```bash
vault-cockpit inbox --json
```

For each entry:

- **item**: read the item file and the RAW evidence in its `sources` (the
  human's verbatim answer). Update the affected wiki pages (`pages`, links in
  the text) as the vault schema requires, citing the RAW file. Then:
  `vault-cockpit done <id>`.
- **decision**: the cockpit created the page with the verbatim statement and
  placeholder sections. Fill in *Assumptions and facts*, *Rejected options*,
  *Expected outcome* and *Review when* from the sources and context. Mark the
  triggers you propose with the vault's unconfirmed marker so the human
  confirms them in the cockpit. Never change `status`. Then:
  `vault-cockpit done <slug>`.

Report what you changed per entry. If an answer contradicts a page, say so
instead of choosing silently.

## 2. Record new open questions

Instead of editing the overview page (it is generated), add items:

```bash
vault-cockpit add "Which product category goes first?" --page shop-relaunch --origin "Ingest quote 2026-07-10"
vault-cockpit add "Trigger may have fired" --decision 2026-09-02-hosted-search --text "..."
```

Use `--decision <slug>` for review hints on the register: a contradiction or a
"review when" trigger that may have fired. Never set a decision's status yourself.

## 3. Propose triage

When an item looks resolved, stale or duplicated, propose; the human decides
in the Triage view:

```bash
vault-cockpit suggest O-0042 done "The 2026-10-12 meeting notes record the answer."
vault-cockpit suggest O-0043 stale "The meeting on 2026-10-02 is over."
vault-cockpit suggest O-0044 duplicate "Same question as O-0012."
```

Give a reason a human can check in one glance, with the source.

## Rules

- Never edit RAW files, never close items, never change decision status.
- If a command fails its checks, nothing was written: fix the cause, retry.
- `vault-cockpit list` shows what is open; `--json` for details.
