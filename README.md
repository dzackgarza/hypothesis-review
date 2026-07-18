# hypothesis-review

A git-anchored, agent-driven review loop over self-hosted [Hypothesis](https://web.hypothes.is/).

Annotate a docs site (or any web page / PDF) in the browser, press **Send to agent**, and an
agent receives the whole batch as one coherent unit, records it in an append-only ledger
committed to the *reviewed* repo — each note stamped with the exact commit it was written
against — acts on it, and marks it resolved. Feedback can't be silently dropped or
half-applied, and any past note can be rewound to the document state it targeted.

- **`annotate` CLI** — `wait` / `pull` / `slice` / `ledger` / `resolve` / `status` /
  `rewind` / `delta` over the Hypothesis API + git.
- **Browser-extension fork** — a small injected floating "Send to agent" button (no
  Hypothesis-client fork).

**Status:** design phase. See [`DESIGN.md`](DESIGN.md). No implementation yet.
