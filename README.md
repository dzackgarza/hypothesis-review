# hypothesis-review

A git-anchored, agent-driven review loop over self-hosted [Hypothesis](https://web.hypothes.is/).

Annotate a docs site, paper, web app, or PDF, then turn on **Send to agent**. The glowing toggle adds every unacted annotation in the review group to an ephemeral queue; turning it off removes the queue flag.
The agent reads the active queue and drains one or more items after applying each remediation.
Every drained item enters a git-tracked JSONL ledger with the remediation explanation before the CLI replaces its queue flag with `acted`.

- **`annotate` CLI** — `annotate` (or `annotate queue`) prints the active queue as JSON. `annotate drain --item ANNOTATION_ID REMEDIATION` accepts one or more item/remediation pairs, appends them to the ledger, commits it, and marks each annotation acted.
  Draining fails outside a git repository; there is no unrecorded mode.

- **Backend-owned normalization** — `h` normalizes highlighted prose and mathematics at annotation intake.
  The CLI reads that canonical value and fails explicitly if a legacy highlight has not been reconciled; it never substitutes the flattened browser quote.

- **Browser-extension and client forks** — the persistent toolbar toggle reports its on/off state and active queue count.

**Status:** implemented.
See [`DESIGN.md`](DESIGN.md).
