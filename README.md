# hypothesis-review

A git-anchored, agent-driven review loop over self-hosted [Hypothesis](https://web.hypothes.is/).

Annotate a docs site, paper, web app, or PDF, then turn on **Send to agent**. New annotations created while the toggle glows receive the `agent:queue` flag; turning it off leaves queued annotations alone and stops flagging new ones.
The agent reads the active queue and drains one or more items after applying each remediation.
Every drained item enters a git-tracked JSONL ledger with the remediation explanation before the CLI deletes the annotation from Hypothesis.

- **`annotate` CLI** — `annotate` (or `annotate queue`) prints the active queue as JSON. `annotate drain --item ANNOTATION_ID REMEDIATION` accepts one or more item/remediation pairs, appends them to the ledger, commits it, and deletes each drained annotation.
  Draining fails outside a git repository; there is no unrecorded mode.

- **Backend-owned normalization** — `h` normalizes highlighted prose and mathematics at annotation intake.
  The CLI reads that canonical value and fails explicitly if a legacy highlight has not been reconciled; it never substitutes the flattened browser quote.

- **Browser-extension and client forks** — the toolbar toggle glows while new annotations will be queued, and each queued annotation displays its flag.

**Status:** implemented.
See [`DESIGN.md`](DESIGN.md).
