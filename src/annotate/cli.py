"""annotate CLI.

The tool forces one thing: every batch of feedback an agent receives is recorded in a
git-tracked ledger. ``wait`` and ``pull`` record-then-print (they bounce unless run inside
a git repo — there is no unrecorded mode); ``slice`` is a read-only ad-hoc view that points
at ``record``; ``record`` appends chosen annotations. Reads go through
:mod:`annotate.source`, marker writes through :mod:`annotate.api`; both are injected via the
click context object so tests swap in stubs.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import click
import httpx
import psycopg

from annotate.api import HClient
from annotate.config import Config
from annotate.ledger import (
    DEFAULT_LEDGER,
    append as ledger_append,
    repo_root as ledger_repo_root,
    resolve as ledger_resolve,
    track as ledger_track,
)
from annotate.models import Annotation, Batch, LedgerEntry
from annotate.session import ACTED, OPEN, SEND, NoSend, batch_for, latest_open
from annotate.source import AnnotationSource, PostgresSource

POLL_SECONDS = 2


@dataclasses.dataclass
class App:
    """Wired dependencies stashed on ``ctx.obj``; tests inject a stub App."""

    source: AnnotationSource
    client: HClient
    group_id: str
    cfg: Config = dataclasses.field(default_factory=Config)


def _current_batch(source: AnnotationSource, group_id: str) -> Batch | None:
    """The sent batch of the latest open session, or ``None`` when there is no
    open marker or the session has not been sent yet."""
    anns = source.list(group_id)
    open_marker = latest_open(anns)
    if open_marker is None:
        return None
    try:
        return batch_for(anns, open_marker)
    except NoSend:
        return None


def _anns_json(anns: list[Annotation]) -> str:
    return json.dumps([dataclasses.asdict(a) for a in anns], default=str)


def _ledger_path_option(f: Callable[..., Any]) -> Callable[..., Any]:
    """Shared ``--path`` option for the recording commands."""
    return click.option(
        "--path",
        "rel_path",
        default=None,
        help="Ledger file, relative to the repo root. Defaults to feedback/ledger.jsonl; "
        "name it per workflow (e.g. research-intake.jsonl). Created and tracked if absent.",
    )(f)


def _require_repo() -> pathlib.Path:
    """Preflight: the working directory must be inside a git repo, else bounce. Feedback is
    only worth capturing if it is tracked alongside the work it concerns."""
    root = ledger_repo_root(pathlib.Path.cwd())
    if root is None:
        raise click.ClickException(
            "not inside a git repository. annotate records every batch of feedback in a "
            "git-tracked ledger so it stays auditable alongside the work it concerns. Run "
            "this from within the repo you are reviewing (or `git init` one first). There "
            "is no unrecorded mode."
        )
    return root


def _record(anns: list[Annotation], rel_path: str | None) -> list[LedgerEntry]:
    """Append annotations to the ledger (deduped by id) and commit; log to stderr where
    they landed. Returns the newly recorded entries."""
    root = _require_repo()
    ledger_path = ledger_resolve(pathlib.Path(rel_path) if rel_path else DEFAULT_LEDGER, root)
    new = ledger_append(anns, ledger_path)
    if new:
        ledger_track(
            ledger_path,
            f"feedback: record {len(new)} annotation(s) in {ledger_path.relative_to(root)}",
        )
    note = f"[annotate] recorded {len(new)} new of {len(anns)} annotation(s) -> {ledger_path}"
    if rel_path is None:
        note += " (default ledger -- all feedback is recorded here)"
    click.echo(note, err=True)
    return new


def _deliver(anns: list[Annotation], rel_path: str | None) -> None:
    """Record the delivered annotations, then print the batch to stdout. Recording runs
    first, so feedback can never reach the agent unrecorded."""
    _record(anns, rel_path)
    click.echo(_anns_json(anns))


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    """Git-anchored review loop over self-hosted Hypothesis annotations.

    Workflow: `wait` opens a review session and blocks until you press Send in the browser;
    it records the whole batch of annotations to a git-tracked ledger and prints it. You act
    on the feedback, then `resolve` tags it done. `slice` browses annotations made outside a
    session and `record` preserves the ones worth keeping.

    Recording is not optional: every command that hands feedback to an agent writes it to the
    ledger (default feedback/ledger.jsonl at the repo root) and commits it first, and refuses
    to run outside a git repo -- feedback stays auditable alongside the work it concerns. Run
    `annotate doctor` to check your setup.
    """
    if ctx.obj is None:
        cfg = Config.load()
        ctx.obj = App(
            source=PostgresSource(cfg.pg_dsn),
            client=HClient(cfg.api_url, cfg.token),
            group_id=cfg.group_id,
            cfg=cfg,
        )


@main.command()
@_ledger_path_option
@click.pass_obj
def pull(app: App, rel_path: str | None) -> None:
    """Record and print the current open session's batch as JSON (or `[]`)."""
    batch = _current_batch(app.source, app.group_id)
    _deliver(batch.annotations if batch else [], rel_path)


@main.command()
@click.option(
    "--timeout",
    default=300,
    show_default=True,
    help="Max seconds to wait for the review:send marker.",
)
@_ledger_path_option
@click.pass_obj
def wait(app: App, timeout: int, rel_path: str | None) -> None:
    """Open a session, wait for Send, then record and print the batch JSON."""
    _require_repo()  # bounce before opening a session whose batch we could not record
    app.client.create_marker(app.group_id, OPEN)
    for _ in range(max(1, timeout // POLL_SECONDS)):
        batch = _current_batch(app.source, app.group_id)
        if batch is not None:
            _deliver(batch.annotations, rel_path)
            return
        time.sleep(POLL_SECONDS)  # ponytail: fixed 2s poll; make it a flag if DB load matters
    raise click.ClickException(f"timed out after {timeout}s waiting for {SEND!r}")


def _not_marker(ann: Annotation) -> bool:
    """A real annotation: not a ``review:open``/``review:send`` session marker."""
    return not (ann.is_marker(OPEN) or ann.is_marker(SEND))


_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def _parse_last(last: str) -> timedelta:
    """Parse a ``\\d+[smhd]`` relative window (e.g. ``2h``) into a timedelta."""
    m = _DURATION.match(last)
    if m is None:
        raise click.BadParameter(
            f"expected <int>[smhd], got {last!r}", param_hint="--last"
        )
    return timedelta(**{_UNIT[m.group(2)]: int(m.group(1))})


@main.command(name="slice")
@click.option(
    "--since",
    type=click.DateTime(),
    default=None,
    help="Only annotations created after this (exclusive).",
)
@click.option(
    "--until",
    type=click.DateTime(),
    default=None,
    help="Only annotations created at/before this (inclusive).",
)
@click.option(
    "--last",
    default=None,
    help="Relative window like 30m/2h/7d; sets --since to now minus it.",
)
@click.option("--uri", default=None, help="Only annotations on this page URI.")
@click.pass_obj
def slice_(
    app: App,
    since: datetime | None,
    until: datetime | None,
    last: str | None,
    uri: str | None,
) -> None:
    """Print real annotations in a time window as JSON (read-only view; ignores markers).

    A convenience for browsing ad-hoc annotations made outside a session. It records
    nothing; preserve the ones worth acting on with ``annotate record <id>...``.
    """
    if last is not None:
        if since is not None:
            raise click.UsageError("--last and --since are mutually exclusive")
        since = datetime.now() - _parse_last(last)
    anns = [a for a in app.source.list(app.group_id, since, until) if _not_marker(a)]
    if uri is not None:
        anns = [a for a in anns if a.uri == uri]
    click.echo(_anns_json(anns))
    if anns:
        click.echo(
            f"[annotate] {len(anns)} shown, not recorded. Preserve the relevant ones with:\n"
            f"    annotate record {' '.join(a.id for a in anns)}",
            err=True,
        )


@main.command()
@_ledger_path_option
@click.argument("annotation_ids", nargs=-1, required=True)
@click.pass_obj
def record(app: App, rel_path: str | None, annotation_ids: tuple[str, ...]) -> None:
    """Append specific annotations (by id) to the ledger and commit.

    The record step for the ad-hoc ``slice`` view: you choose which notes to preserve, then
    hand their ids here. Prints the newly recorded entries as JSON.
    """
    by_id = {a.id: a for a in app.source.list(app.group_id)}
    missing = [i for i in annotation_ids if i not in by_id]
    if missing:
        raise click.ClickException(f"annotation id(s) not in the group: {', '.join(missing)}")
    new = _record([by_id[i] for i in annotation_ids], rel_path)
    click.echo(json.dumps([dataclasses.asdict(e) for e in new], default=str))


@main.command()
@click.pass_obj
def resolve(app: App) -> None:
    """Tag the current batch's annotations `acted` via the h API."""
    batch = _current_batch(app.source, app.group_id)
    anns = batch.annotations if batch else []
    for ann in anns:
        app.client.tag(ann.id, [ACTED])
    click.echo(f"tagged {len(anns)} annotation(s) {ACTED!r}")


def _exact_quotes(target: Any) -> list[str]:
    """The TextQuoteSelector ``exact`` strings from an h ``target`` payload."""
    quotes: list[str] = []
    for t in target or []:
        if not isinstance(t, dict):
            continue
        for sel in t.get("selector") or []:
            if (
                isinstance(sel, dict)
                and sel.get("type") == "TextQuoteSelector"
                and sel.get("exact")
            ):
                quotes.append(sel["exact"])
    return quotes


def _build_text(root: pathlib.Path) -> str:
    """Concatenated text of every file under ``root`` (the current build)."""
    # ponytail: naive whole-tree read; map uri->path if builds get large.
    parts: list[str] = []
    for p in root.rglob("*"):
        if p.is_file():
            parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


@main.command()
@click.option(
    "--root",
    type=click.Path(exists=True, file_okay=False, path_type=pathlib.Path),
    default=None,
    help="Built-site root; flag open annotations whose quote no longer matches.",
)
@click.pass_obj
def status(app: App, root: pathlib.Path | None) -> None:
    """Count open vs acted annotations; with `--root`, flag drifted quotes."""
    reals = [a for a in app.source.list(app.group_id) if _not_marker(a)]
    open_anns = [a for a in reals if not a.is_marker(ACTED)]
    acted = [a for a in reals if a.is_marker(ACTED)]
    click.echo(f"open={len(open_anns)} acted={len(acted)}")
    if root is not None:
        build = _build_text(root)
        for a in open_anns:
            quotes = _exact_quotes(a.target)
            matches = bool(quotes) and all(q in build for q in quotes)
            click.echo(f"{'match' if matches else 'drift'}\t{a.id}\t{a.uri}")


def _h_reachable(api_url: str) -> str:
    resp = httpx.get(f"{api_url.rstrip('/')}/api/", timeout=3.0)
    return f"reachable (HTTP {resp.status_code})"


def _pg_reachable(dsn: str) -> str:
    with psycopg.connect(dsn, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return "reachable"


def _probe(label: str, thunk: Callable[[], str]) -> tuple[bool, str, str]:
    """Run a diagnostic connection probe and render its outcome as a status row. A doctor
    check must report an unreachable dependency as status, not crash on it -- the tool's one
    sanctioned error-to-status boundary."""
    try:
        return True, label, thunk()
    except (httpx.HTTPError, psycopg.Error, OSError) as exc:
        return False, label, (str(exc).splitlines() or [type(exc).__name__])[0]


@main.command()
@click.pass_obj
def doctor(app: App) -> None:
    """Check readiness: git repo (so feedback can be recorded), config, and the h backend.

    Prints one status line per check and exits non-zero if any fails, so an agent can gate a
    session on it (``annotate doctor && annotate wait``).
    """
    cfg = app.cfg
    rows: list[tuple[bool, str, str]] = []

    root = ledger_repo_root(pathlib.Path.cwd())
    if root is None:
        rows.append(
            (False, "git repo", "not inside one -- no feedback can be recorded; cd into the "
             "repo you are reviewing, or run `git init`")
        )
    else:
        rows.append((True, "git repo", f"{root} (feedback -> {ledger_resolve(DEFAULT_LEDGER, root)})"))

    missing = [n for n in ("group_id", "token") if not getattr(cfg, n)]
    if missing:
        rows.append(
            (False, "config", f"missing {', '.join(missing)} in ~/.config/annotate/config.toml")
        )
    else:
        rows.append((True, "config", f"group {cfg.group_id}, api {cfg.api_url}"))

    rows.append(_probe("h API", lambda: _h_reachable(cfg.api_url)))
    rows.append(_probe("Postgres", lambda: _pg_reachable(cfg.pg_dsn)))

    for ok, label, detail in rows:
        click.echo(f"[{'OK' if ok else 'FAIL'}] {label}: {detail}")
    if not all(ok for ok, _, _ in rows):
        raise click.ClickException("not ready -- resolve the FAIL line(s) above")
