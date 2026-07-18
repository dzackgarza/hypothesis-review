# hypothesis-review

A git-anchored, agent-driven review loop over self-hosted [Hypothesis](https://web.hypothes.is/).

Annotate anything in the browser while working on a project — a docs site, a paper, a web
app, a PDF — press **Send to agent**, and an agent receives the whole batch as one coherent
unit, appends it to a git-tracked JSONL ledger in the repo you're working in, acts on it, and
marks it resolved. Feedback can't be silently dropped or half-applied. The ledger is
committed alongside the code, so git's own history anchors every note to the state it landed
against.

- **`annotate` CLI** — `wait` / `pull` / `slice` / `ledger` / `resolve` / `status` over the
  Hypothesis API + git.
- **Browser-extension fork** — a small injected floating "Send to agent" button (no
  Hypothesis-client fork).

**Status:** implemented. See [`DESIGN.md`](DESIGN.md).
