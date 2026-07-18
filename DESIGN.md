# Annotation → Ledger Review Loop — Design

**Status:** design (pre-implementation). Date: 2026-07-18.

## Purpose

An opinionated review loop for annotating documents — a docs site, and any web page/PDF —
where feedback is captured in the browser, **batched**, handed to an agent as a coherent
unit, acted on, and preserved in a **git-anchored, replayable, auditable ledger**. It
exists to defeat a specific failure model: feedback that is dropped, only partially applied,
or lost when the document mutates under the reviewer — and to make any past feedback
**rewindable to the exact document state it was written against**.

## Non-goals

- Multi-user / concurrent review (solo for now).
- Reimplementing annotation capture or text anchoring — Hypothesis owns that.
- A long-lived listener daemon: the agent is woken by its harness, not a persistent socket.

## The loop

```
agent opens session  →  reviewer annotates freely (any pages)  →  reviewer presses Send
    →  agent `wait` returns the whole window  →  tee to git ledger (commit-stamped)
    →  agent acts (edit + commit)  →  tag the batch `acted`  →  document updates
    →  next session opens clean
```

The **drain-before-act** beat is the load-bearing invariant: the agent is structurally
forbidden from acting mid-review, so the page never mutates under the reviewer's feet.

## Session model (window-bounded)

A feedback session is an explicit **`[open, close]` window**, opened by the agent and closed
by the reviewer.

- **Open (agent):** `annotate wait` posts a **session-open marker** to the review group and
  parks (a harness-tracked background job). The marker is the lower bound and records the
  **open-commit** — the document state at session start.
- **Annotate (reviewer):** freely, across as many pages/PDFs as desired. Every annotation
  lands in the group with its own `target_uri`; nothing is scoped to a single page.
- **Close (reviewer):** the Send button posts the **drain marker** — the upper bound.
- **Batch = every annotation in the group created strictly between the two markers,
  regardless of page.** Cross-page capture is automatic because the filter is the *window*,
  not the URL.
- The agent tees the batch, acts, and tags each annotation **`acted`**, so the next session
  opens clean and a processed batch can never be re-swept.

The two markers bound the session in `h` and in the ledger (open-commit … send-commit); the
`acted` tag is the lifecycle flag. Annotations made while no session is open are **not**
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
- The Send marker doubles as the **ledger batch header**: its send-time timestamp maps to
  the commit-at-send-time. Act-signal and audit-record are one object.

### 4. The CLI (`annotate`, Python)
Opinionated glue over the h API + git:
- `wait` — opens a session (posts the open marker) and blocks as a **background job** until
  the drain marker; exits with the batch on stdout (the harness wakes the same agent).
  Subscribes to h's websocket (`:5001`) or polls `/api/search`.
- `pull` — the current window's batch as structured JSON.
- `slice [--since T] [--until T] [--last DUR] [--uri PAT]` — **read-only, ad-hoc**: return
  annotations in an arbitrary datetime window, ignoring markers. The escape hatch for
  "annotated a paper as I read, never opened a session, now synthesize what I marked in the
  last hour" — timing alone carries the intent.
- `ledger` — append the batch to the git-tracked JSONL ledger, each annotation stamped with
  the send-time commit + its W3C target/selector (the content-at-the-time).
- `resolve` — tag the batch **`acted`** (non-destructive) so it can't re-fire.
- `status` — pending / open / resolved; per open annotation, whether its anchor still
  resolves in the current build (drift/thrash detection).
- `rewind <id>` / `delta <id>` — `git checkout` / `git diff <commit>..HEAD` on the
  annotation's send-time commit.

### 5. The ledger
Append-only JSONL, git-tracked (in the *reviewed* repo). Each entry is
[W3C Web Annotation](https://www.w3.org/TR/annotation-model/)-shaped:

```
{ id, session, created,
  target: { source, selector: [TextQuote{exact,prefix,suffix}, TextPosition, Range] },
  body, commit,
  state: open | acted | wontfix,
  resolution: { commit, note } }
```

### 6. Version-anchoring
- The reviewed repo's deploy writes a **deploy-log** (commit + timestamp per deploy).
- The tee joins each marker's send-time against the deploy-log → the commit the feedback was
  written against (never the tee-time HEAD).
- Rewind = `git checkout <commit>`; delta = `git diff <commit>..HEAD -- <page-source>`.

### 7. Agent callback
`wait` runs as a harness-tracked background job; exits with the batch on stdout; the harness
notifies the same session. No PTY, no daemon, no `nohup`/`&`.

## Settled decisions

- **Repo home:** this repo — a standalone public repo (`hypothesis-review`) holding the CLI
  and the extension fork. The **ledger lives in the reviewed repo**, not here.
- **Resolve mechanism:** a non-destructive **`acted` tag**.
- **Session scope:** window-bounded (`[open, close]` markers); `slice` covers the ad-hoc
  no-session case.

## Open for the implementation plan

- Extension fork layout: GitHub fork of `hypothesis/browser-extension` vs an overlay/subdir
  here.
- `wait` transport: h websocket vs polling `/api/search` (marker convention is
  transport-agnostic; start with whichever is less plumbing).
