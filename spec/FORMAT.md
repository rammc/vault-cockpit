# Format: open items and decisions

Version 0.1. Plain markdown files with YAML frontmatter, readable without the cockpit.
Field names and status values below are the defaults; a vault can rename them in
`cockpit.json` (`item_fields`, `item_status`, `decision_fields`, `decision_status`).
Machine-readable schemas: [`open-item.schema.json`](open-item.schema.json),
[`decision.schema.json`](decision.schema.json), [`cockpit.schema.json`](cockpit.schema.json).

## Principles

1. **Evidence first.** Whatever a human says (an answer, a decision, a reason) is
   stored verbatim as a new file under `RAW/` before any wiki page changes. RAW
   files are created, never edited.
2. **Humans decide validity.** An agent may propose, suggest and draft. Only a
   human sets a decision to `valid`, `superseded` or `withdrawn`, or closes an item.
3. **Nothing is deleted.** Closed items keep their file and history; superseded
   decisions stay linked to their successor.
4. **All or nothing.** Every change is one transaction: RAW, wiki pages, log,
   generated overview, checks. If a check fails, every file is restored.
5. **Generated overview.** The overview page (`open.md`) is built from the item
   files and never edited by hand.

## Open item — `Wiki/open/O-0001.md`

```yaml
---
id: O-0001
title: "Payment at launch: cards only, invoice, or both?"
type: open
domain: work
status: open            # open | waiting | answered | discarded | duplicate
owner: me
created: 2026-10-17
due:                    # set while status is waiting
origin: "Ingest of the quote for the shop relaunch"
pages: [shop-relaunch, payment-options]
decision:               # slug of a decision this item is a review hint for
suggestion:             # done | stale | duplicate, proposed by an agent
suggestion_reason:
duplicate_of:
integrate: false        # true: answered, waiting for the agent to work it in
updated: 2026-10-17
sources: []             # RAW evidence, appended by every answer
---

Free text with [[wiki links]].

## History

- 2026-10-17 created in the cockpit
- 2026-10-18 answered, queued for integration (evidence: RAW/chat/2026-10-18-o-0001.md)
```

Lifecycle:

```
open ──answer──▶ answered (integrate: true) ──agent: done──▶ answered
  │  ──answer, still open──▶ waiting (due, integrate: true) ──due date──▶ back on the open list
  │  ──later───▶ waiting ──due date──▶ back on the open list
  │  ──discard─▶ discarded
  └  ──dup─────▶ duplicate (duplicate_of)
```

## Decision — `Wiki/<domain>/decisions/2026-10-09-<slug>.md`

```yaml
---
title: "Use the hosted search"
type: decision
domain: work
status: proposed        # proposed | valid | superseded | withdrawn
decided_by: Alex Example
decided_on: 2026-09-02
subject: search   # at most one valid decision per subject
supersedes: []
superseded_by:
updated: 2026-10-09
sources:
  - RAW/decisions/2026-09-02-hosted-search.md
---
```

Required sections: **Decision** (verbatim, cited), **Assumptions and facts**,
**Rejected options**, **Expected outcome**, **Review when** (observable triggers),
**References**. Agent-proposed triggers carry a marker line (configurable via
`unconfirmed_trigger_marker`) until a human confirms them.

Review actions: `confirm` (proposed → valid), `keep` (acknowledge a review hint),
`supersede` (new page with the same subject; old page gets `superseded_by`),
`withdraw`. Each writes a RAW statement and closes the linked review hints.

## RAW evidence

Answers: `RAW/chat/<date>-<id>.md` with `title`, `date`, `from`, `channel`,
`regarding` and the answer as a block quote. Decisions:
`RAW/decisions/<date>-<slug>.md` with `title`, `date`, `decided_by`, `channel`,
`note` and the statement as a block quote. Triage: one RAW note per batch.

## Log

Every transaction appends to the log page:

```
## [2026-10-17] cockpit | Wiki/open/O-0001.md
O-0001 answered (evidence: RAW/chat/2026-10-17-o-0001.md).
```
