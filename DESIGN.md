# Annotation → Ledger Review Loop — Design

**Status:** implemented. Date: 2026-07-18.

## Purpose

An opinionated review loop for capturing web feedback while working on **any** project — a
docs site, a paper, a web app, or anything served in a browser. Feedback is captured in the
browser, **batched**, handed to an agent as a coherent unit, acted on, and appended to a
**git-tracked JSONL ledger in the ambient repo**. It exists to defeat a specific failure
model: feedback that is dropped, only partially applied, or lost track of. The ledger is
committed alongside the code, so git's own history places every entry next to the project
state it landed against — an agent cross-references from there; the tool does not
reimplement `git checkout` / `git diff`.

## Non-goals

- Multi-user / concurrent review (solo for now).
- Reimplementing annotation capture or text anchoring — Hypothesis owns that.
- A long-lived listener daemon: the agent is woken by its harness, not a persistent socket.

## The loop

```
agent opens session  →  reviewer annotates freely (any pages)  →  reviewer presses Send
    →  agent `wait` records the whole window to the git-tracked ledger, returns it
    →  agent acts (edit + commit)  →  tag the batch `acted`  →  document updates
    →  next session opens clean
```

The **drain-before-act** beat is the load-bearing invariant: the agent is structurally
forbidden from acting mid-review, so the page never mutates under the reviewer's feet.

## Session model (window-bounded)

A feedback session is an explicit **`[open, close]` window**, opened by the agent and closed
by the reviewer.

- **Open (agent):** `annotate wait` posts a **session-open marker** to the review group and
  parks (a harness-tracked background job). The marker is the window's lower bound.
- **Annotate (reviewer):** freely, across as many pages/PDFs as desired. Every annotation
  lands in the group with its own `target_uri`; nothing is scoped to a single page.
- **Close (reviewer):** the Send button posts the **drain marker** — the upper bound.
- **Batch = every annotation in the group created strictly between the two markers,
  regardless of page.** Cross-page capture is automatic because the filter is the *window*,
  not the URL.
- The agent tees the batch, acts, and tags each annotation **`acted`**, so the next session
  opens clean and a processed batch can never be re-swept.

The two markers bound the session window in `h`; the `acted` tag is the lifecycle flag. Annotations made while no session is open are **not**
swept by the next session's window — for that ad-hoc case, see `slice`.

## Components

### 1. Capture — self-hosted Hypothesis
- `h` runs as the `hypothesis` user systemd service (`localhost:5000`); annotations land in
  a **private group**.
- The browser extension is forked (below), built pointed at `localhost:5000`.
- Hypothesis owns robust text-quote anchoring (exact + prefix/suffix + position) and
  **orphan detection** (content changed → the annotation visibly detaches) — reused, not
  rebuilt; that detachment is already half the drift/thrash signal.

### 2. Drain gesture — injected floating button (browser-extension fork; small)
- A **shadow-DOM-isolated floating button**, fixed on the **LHS** (opposite the sidebar),
  injected on activation, torn down on deactivate. Shows a **pending count**.
- Hooks: `src/background/sidebar-injector.ts` (injection), `tab-state.ts` (active-tab
  tracking → mount/unmount), `messages.ts` (click → service worker).
- Scope: one module in `src/background/` + the worker's drain handler + the badge. **No
  `hypothesis/client` fork** — the sidebar (npm `hypothesis`) is untouched.

### 3. Signal — marker annotations
- Open and Send each post a **marker annotation** (reserved tag convention) to the group.
- The Send marker doubles as the **batch upper bound**: everything back to the open marker is
  the batch. Act-signal and window-bound are one object.

### 4. The CLI (`annotate`, Python)
Opinionated glue over the h API + git. Every feedback-delivering command **preflights** the
git repo and **bounces** if you're not in one — recording is not optional, so there is no
unrecorded mode. `--path` names a per-workflow ledger (still repo-root-relative); omit it for
the canonical default `feedback/ledger.jsonl`, whose absolute path is logged to stderr so the
agent knows where feedback lands. Recording dedups by annotation id, so re-runs never double it.
- `wait [--path P]` — opens a session (posts the open marker) and blocks as a **background
  job** until the drain marker; **records the batch to the ledger, then** exits with it on
  stdout (the harness wakes the same agent).
- `pull [--path P]` — records and prints the current window's batch as structured JSON.
- `slice [--since T] [--until T] [--last DUR] [--uri PAT]` — **read-only, ad-hoc** view:
  annotations in an arbitrary datetime window, ignoring markers. The escape hatch for
  "annotated a paper as I read, never opened a session, now synthesize what I marked in the
  last hour." It records nothing itself; it prints the ids and an `annotate record` line to
  preserve the ones worth keeping.
- `record <id>... [--path P]` — append specific annotations (by id) to the ledger and commit;
  the record step for the ad-hoc `slice` view, where the agent chooses what to preserve.
- `resolve` — tag the batch **`acted`** (non-destructive) so it can't re-fire.
- `status` — pending / open / resolved; per open annotation, whether its anchor still
  resolves in the current build (drift/thrash detection).

### 5. The ledger
Append-only JSONL, git-tracked in the ambient repo at an agent-named path. Each entry records
the annotation itself (h's [W3C Web Annotation](https://www.w3.org/TR/annotation-model/)
target reassembled from the DB); git's commit history is the anchor:

```
{ id, created, uri,
  text,
  tags,
  target: [{ source, selector: [TextQuoteSelector{exact}, …] }] }
```

### 6. Anchoring
The ledger is committed, so its own git history places each entry next to the code state it
landed against — no deploy-log, no timestamp→commit table, nothing deploy-specific, and it
works for content that is never deployed (a paper, a purely-local book build). An agent that
needs "what did this look like when the note was written" cross-references git directly
(`git log` the ledger, `git checkout`, `git diff`); the tool does not wrap those.

### 7. Agent callback
`wait` runs as a harness-tracked background job; exits with the batch on stdout; the harness
notifies the same session. No PTY, no daemon, no `nohup`/`&`.

## Settled decisions

- **Repo home:** this repo — a standalone public repo (`hypothesis-review`) holding the CLI
  and the extension fork. The **ledger lives in the ambient repo being worked on** (the CWD's
  git repo), at an agent-named path, never here.
- **Resolve mechanism:** a non-destructive **`acted` tag**.
- **Session scope:** window-bounded (`[open, close]` markers); `slice` covers the ad-hoc
  no-session case.

## Open for the implementation plan

- Extension fork layout: GitHub fork of `hypothesis/browser-extension` vs an overlay/subdir
  here.
- `wait` transport: h websocket vs polling `/api/search` (marker convention is
  transport-agnostic; start with whichever is less plumbing).
