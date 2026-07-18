# annotate CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `annotate` CLI that turns self-hosted Hypothesis annotations into a batched, git-anchored, auditable review loop.

**Architecture:** A Python package exposing an `annotate` command. It **reads** annotations directly from the h Postgres DB (reliable, no ES-index dependency) behind a swappable source interface, and **writes** (session markers, `acted` tags) through the h HTTP API with a developer token. Sessions are `[open, send]` marker windows; batches append to a JSONL ledger in the *reviewed* repo, each entry stamped with the commit that was live at annotation time via a deploy-log join.

**Tech Stack:** Python 3.11+, `click` (CLI), `psycopg[binary]` (Postgres reads), `httpx` (API writes), `pytest`. Packaged with `uv`/`pyproject.toml`.

## Global Constraints

- Python **3.11+**; package + run via `uv` (`uvx`-installable console script `annotate`).
- **Reads never go through `/api/search`** (ES-index gap observed); reads are Postgres-direct behind `AnnotationSource`. Writes go through the h HTTP API.
- h connection: Postgres at `127.0.0.1:5432` (db `postgres`, user `postgres`, trust auth, no password); API at `http://localhost:5000`; group id and API token from config.
- Config precedence: env (`ANNOTATE_*`) over `~/.config/annotate/config.toml`. Bespoke config is **TOML**, parsed with `tomllib`.
- The ledger is written into the **reviewed repo** (path from config/CWD), never into this repo.
- No secrets in code or argv; the API token is read from config/env only.

---

## File structure

- `pyproject.toml` — package metadata, deps, `annotate` console script.
- `justfile` — real QC (replaces the design-phase no-op): `test-commit` → lint+type+test.
- `src/annotate/config.py` — `Config` (h URL, pg DSN, group id, token, ledger path, deploy-log path); env+TOML load.
- `src/annotate/models.py` — `Annotation`, `Marker`, `Batch`, `LedgerEntry` dataclasses + JSON (de)serialization.
- `src/annotate/source.py` — `AnnotationSource` protocol + `PostgresSource` (reads the `annotation` table).
- `src/annotate/api.py` — `HClient` (API writes: `create_marker`, `tag`).
- `src/annotate/session.py` — marker/window logic: batch = annotations strictly between the open and send markers.
- `src/annotate/ledger.py` — JSONL append + read; `LedgerEntry` from `Annotation`.
- `src/annotate/anchor.py` — deploy-log join (timestamp→commit), `rewind`, `delta` via `git`.
- `src/annotate/cli.py` — `click` commands: `wait pull slice ledger resolve status rewind delta`.
- `tests/` — one test module per source module; fixtures seed a throwaway schema-compatible sqlite/pg or use a stubbed source.

---

## Task 1: Package skeleton, config, real QC

**Files:**
- Create: `pyproject.toml`, `src/annotate/__init__.py`, `src/annotate/config.py`, `tests/test_config.py`
- Modify: `justfile`

**Interfaces:**
- Produces: `Config` with fields `api_url: str`, `pg_dsn: str`, `group_id: str`, `token: str`, `ledger_path: pathlib.Path`, `deploy_log: pathlib.Path`; classmethod `Config.load() -> Config`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import os, pathlib
from annotate.config import Config

def test_env_overrides_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("ANNOTATE_GROUP_ID", "abc123")
    monkeypatch.setenv("ANNOTATE_TOKEN", "6879-secret")
    monkeypatch.setenv("ANNOTATE_LEDGER_PATH", str(tmp_path / "ledger.jsonl"))
    cfg = Config.load()
    assert cfg.group_id == "abc123"
    assert cfg.token == "6879-secret"
    assert cfg.pg_dsn == "postgresql://postgres@127.0.0.1:5432/postgres"  # default
    assert cfg.ledger_path == tmp_path / "ledger.jsonl"
```

- [ ] **Step 2: Run test to verify it fails** — `uv run pytest tests/test_config.py -v` → FAIL (no module `annotate.config`).

- [ ] **Step 3: Write minimal implementation** — `config.py`: a frozen dataclass; `load()` reads `~/.config/annotate/config.toml` via `tomllib` if present, overlays `ANNOTATE_*` env vars, applies defaults (`api_url=http://localhost:5000`, `pg_dsn=postgresql://postgres@127.0.0.1:5432/postgres`). `pyproject.toml` declares deps (`click`, `psycopg[binary]`, `httpx`) and `[project.scripts] annotate = "annotate.cli:main"`. `justfile` `test-commit` runs `uv run ruff check`, `uv run pyright`, `uv run pytest`.

- [ ] **Step 4: Run test to verify it passes** — `uv run pytest tests/test_config.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat: package skeleton + config"`.

---

## Task 2: Models

**Files:** Create `src/annotate/models.py`, `tests/test_models.py`

**Interfaces:**
- Produces: `Annotation(id, created, userid, group, uri, text, tags, target)`; `is_marker(kind)`; `Batch(open_marker, send_marker, annotations)`; `LedgerEntry` with `.to_json()/.from_json()`. `Annotation.from_pg_row(row: dict) -> Annotation`.

- [ ] **Step 1: Write the failing test** — assert `Annotation.from_pg_row({...})` maps columns (`id, created, userid, groupid, uri→uri, text, tags, target`) and `is_marker("review:open")` is true iff `"review:open"` in `tags`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** the dataclasses + mappers. `target` is the h `target` JSON (selectors) passed through verbatim.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `"feat: annotation/marker/batch/ledger models"`.

---

## Task 3: Postgres annotation source

**Files:** Create `src/annotate/source.py`, `tests/test_source.py`

**Interfaces:**
- Consumes: `Config.pg_dsn`, `Config.group_id`, `Annotation.from_pg_row`.
- Produces: `AnnotationSource` protocol with `list(group_id, since=None, until=None) -> list[Annotation]` (ordered by `created` asc, markers included); `PostgresSource(dsn)`. Query: `SELECT id, created, userid, groupid, uri, text, tags, target FROM annotation WHERE groupid=%s AND deleted=false [AND created > %s] [AND created <= %s] ORDER BY created`.

- [ ] **Step 1: Write the failing test** — use a stub/in-memory source implementing the protocol to test the *interface* contract (ordering, since/until filtering). Gate the real-Postgres test behind `@pytest.mark.pg` (skipped unless `ANNOTATE_PG_IT=1`), seeding two rows and asserting `list()` returns them ordered.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `PostgresSource` with `psycopg`; parameterized query; map rows via `Annotation.from_pg_row`. Convert `tags`/`target` (pg `text[]`/`jsonb`) to Python.
- [ ] **Step 4: Run → PASS** (unit stub always; `ANNOTATE_PG_IT=1 uv run pytest -m pg` against the live DB).
- [ ] **Step 5: Commit** `"feat: Postgres annotation source"`.

---

## Task 4: h API writes (markers + tagging)

**Files:** Create `src/annotate/api.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `Config.api_url`, `Config.token`, `Config.group_id`.
- Produces: `HClient(api_url, token)` with `create_marker(group_id, kind: str) -> str` (POST `/api/annotations` with `{"uri": MARKER_URI, "group": group_id, "text": kind, "tags": [kind]}`, Bearer token, returns new id) and `tag(annotation_id, add: list[str]) -> None` (PATCH `/api/annotations/{id}` merging tags). `MARKER_URI = "urn:annotate:marker"`.

- [ ] **Step 1: Write the failing test** — with `httpx.MockTransport`, assert `create_marker(g, "review:open")` issues `POST /api/annotations` with the JSON body above and `Authorization: Bearer <token>`, and returns the `id` from a stubbed `201` response.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `HClient` on `httpx.Client`. (Verify the live create shape once: `curl -H "Authorization: Bearer <tok>" -X POST localhost:5000/api/annotations -d '{...}'` — adjust body only if the live API rejects it.)
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `"feat: h API client for markers + tagging"`.

---

## Task 5: Session windowing

**Files:** Create `src/annotate/session.py`, `tests/test_session.py`

**Interfaces:**
- Consumes: `list[Annotation]`, `is_marker`.
- Produces: `latest_open(anns) -> Annotation|None`; `batch_for(anns, open_marker) -> Batch` = real (non-marker, non-`acted`) annotations with `open_marker.created < created <= send.created`, where `send` is the first `review:send` marker after `open`. Raises `NoSend` if none yet.

- [ ] **Step 1: Write the failing test** — feed a list `[open@1, a@2 (paperA), b@3 (paperB), send@4, c@5]`; assert `batch_for(list, open)` = `[a, b]` (cross-page, excludes markers and the post-send `c`).
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** the window logic (pure function over the ordered list).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `"feat: session window (open..send) batching"`.

---

## Task 6: `pull` + `wait` commands

**Files:** Create `src/annotate/cli.py`, `tests/test_cli_pull.py`

**Interfaces:**
- Consumes: `Config`, `PostgresSource`, `HClient`, `latest_open`, `batch_for`.
- Produces: `annotate pull` (prints the current open session's batch as JSON, or `[]`); `annotate wait` (posts an open marker via `HClient.create_marker`, then polls `PostgresSource.list` every 2s until a `review:send` marker appears after the open, then prints the batch JSON and exits 0). `wait` is the command the agent runs as a background job.

- [ ] **Step 1: Write the failing test** — with a stub source pre-seeded `[open, a, b, send]`, `CliRunner().invoke(main, ["pull"])` yields JSON `[a, b]`. For `wait`, seed the send marker immediately so the poll returns on the first tick; assert exit 0 + batch JSON.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** the two commands wiring the pieces; `wait` loop uses `time.sleep(2)` and a max-iterations guard from `--timeout`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `"feat: pull + wait commands"`.

---

## Task 7: Ledger + version-anchoring

**Files:** Create `src/annotate/ledger.py`, `src/annotate/anchor.py`, `tests/test_ledger.py`, `tests/test_anchor.py`

**Interfaces:**
- Consumes: `Batch`, `Config.ledger_path`, `Config.deploy_log`.
- Produces: `commit_at(ts, deploy_log) -> str` (returns the commit whose deploy interval contains `ts`; deploy-log lines are `<iso8601>\t<sha>`); `append(batch, ledger_path, deploy_log) -> list[LedgerEntry]` (one JSONL line per annotation, `commit=commit_at(ann.created)`, `state="open"`); `rewind(entry) -> str` (`git rev-parse` check + returns `git checkout` cmd); `delta(entry) -> str` (`git diff <commit>..HEAD -- <source>`).

- [ ] **Step 1: Write the failing test** — `commit_at` with a 2-line deploy log returns the earlier sha for a ts between them; `append` writes N JSONL lines each with the right `commit` and round-trips via `LedgerEntry.from_json`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `anchor.commit_at` (parse deploy-log, bisect by ts) and `ledger.append` (open `ledger_path` in append mode, one `json.dumps` per line). `delta` shells `git` via `subprocess.run` in the reviewed repo.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `"feat: git-anchored ledger + rewind/delta"`.

---

## Task 8: `ledger`, `resolve`, `slice`, `status`, `rewind`, `delta` commands

**Files:** Modify `src/annotate/cli.py`; Create `tests/test_cli_commands.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `annotate ledger` (pull batch → `ledger.append` → print entries); `annotate resolve` (tag the batch `acted` via `HClient.tag`); `annotate slice --since/--until/--last/--uri` (source.list with a datetime window, ignores markers, prints JSON — read-only); `annotate status` (counts open/acted; per open, whether its TextQuote still matches the current build under `--root`); `annotate rewind <id>` / `delta <id>` (look up the ledger entry, print the anchor.* result).

- [ ] **Step 1: Write the failing tests** — one per command against stubs: `slice --last 1h` returns only in-window anns; `resolve` calls `HClient.tag(id, ["acted"])` for each batch member; `status` reports `open=2`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** the commands. `--last` parses durations (`\d+[smhd]`) into a `timedelta`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `"feat: ledger/resolve/slice/status/rewind/delta commands"`.

---

## Task 9: End-to-end smoke against the live instance

**Files:** Create `tests/test_e2e.py` (marked `@pytest.mark.pg`, skipped unless `ANNOTATE_E2E=1`), `docs/USAGE.md`

**Interfaces:** Consumes the whole CLI.

- [ ] **Step 1: Write the test** — `ANNOTATE_E2E=1`: post an open marker (HClient), insert a fake annotation + a send marker (via HClient), run `pull`, assert the fake annotation is in the batch; run `ledger`, assert a JSONL line landed; run `resolve`, assert the `acted` tag is present on re-read.
- [ ] **Step 2: Run → FAIL** (until wired).
- [ ] **Step 3:** write `docs/USAGE.md` — the loop, config, token creation (`http://localhost:5000/account/developer`), and the private-group id.
- [ ] **Step 4: Run** `ANNOTATE_E2E=1 uv run pytest tests/test_e2e.py -v` → PASS.
- [ ] **Step 5: Commit** `"test: e2e smoke + usage docs"`.

---

## Self-review

- **Spec coverage:** `wait`/`pull`/`slice`/`ledger`/`resolve`/`status`/`rewind`/`delta` → Tasks 6, 8; window-bounded sessions → Task 5; marker signal → Task 4; ledger + commit-at-send-time → Task 7; Postgres-read decision → Global Constraints + Task 3. Extension button = separate plan (out of scope here, by design).
- **Placeholders:** none — each task carries its test, command, and the code shape. The two "verify against live" notes (Task 4 create body, Task 9) are external-API confirmations, not design gaps.
- **Type consistency:** `Annotation`, `Batch`, `LedgerEntry`, `AnnotationSource.list`, `HClient.create_marker/tag`, `commit_at`, `batch_for` are named identically across producing/consuming tasks.

## Open (resolve during execution)

- Confirm the live `POST /api/annotations` body (Task 4) and the private group id (create the group in the client, put its id in config).
- Deploy-log production in the reviewed repo (the research docs deploy writes `<iso>\t<sha>`) is a one-line addition to that repo's `docs.yml` — tracked as a follow-up, not part of the CLI.
