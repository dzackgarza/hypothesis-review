"""annotate CLI.

``pull`` prints the current open session's batch as JSON (or ``[]``); ``wait``
posts a ``review:open`` marker, then polls Postgres until the matching
``review:send`` marker lands and prints the batch — the command an agent runs
as a background job. Reads go through :mod:`annotate.source`, the open marker
through :mod:`annotate.api`; both are injected via the click context object so
tests swap in stubs (see ``tests/test_cli_pull.py``).
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import time
from datetime import datetime, timedelta
from typing import Any

import click

from annotate import anchor
from annotate.api import HClient
from annotate.config import Config
from annotate.ledger import append as ledger_append
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


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    """Git-anchored review loop over self-hosted Hypothesis annotations."""
    if ctx.obj is None:
        cfg = Config.load()
        ctx.obj = App(
            source=PostgresSource(cfg.pg_dsn),
            client=HClient(cfg.api_url, cfg.token),
            group_id=cfg.group_id,
            cfg=cfg,
        )


@main.command()
@click.pass_obj
def pull(app: App) -> None:
    """Print the current open session's batch as JSON, or ``[]``."""
    batch = _current_batch(app.source, app.group_id)
    click.echo(_anns_json(batch.annotations if batch else []))


@main.command()
@click.option(
    "--timeout",
    default=300,
    show_default=True,
    help="Max seconds to wait for the review:send marker.",
)
@click.pass_obj
def wait(app: App, timeout: int) -> None:
    """Open a session, then poll until it is sent and print the batch JSON."""
    app.client.create_marker(app.group_id, OPEN)
    for _ in range(max(1, timeout // POLL_SECONDS)):
        batch = _current_batch(app.source, app.group_id)
        if batch is not None:
            click.echo(_anns_json(batch.annotations))
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
    """Print real annotations in a time window as JSON (read-only; ignores markers)."""
    if last is not None:
        if since is not None:
            raise click.UsageError("--last and --since are mutually exclusive")
        since = datetime.now() - _parse_last(last)
    anns = [a for a in app.source.list(app.group_id, since, until) if _not_marker(a)]
    if uri is not None:
        anns = [a for a in anns if a.uri == uri]
    click.echo(_anns_json(anns))


@main.command()
@click.pass_obj
def resolve(app: App) -> None:
    """Tag the current batch's annotations ``acted`` via the h API."""
    batch = _current_batch(app.source, app.group_id)
    anns = batch.annotations if batch else []
    for ann in anns:
        app.client.tag(ann.id, [ACTED])
    click.echo(f"tagged {len(anns)} annotation(s) {ACTED!r}")


@main.command()
@click.pass_obj
def ledger(app: App) -> None:
    """Tee the current batch to the reviewed repo's git-anchored JSONL ledger."""
    batch = _current_batch(app.source, app.group_id)
    if batch is None:
        click.echo("[]")
        return
    entries = ledger_append(batch, app.cfg.ledger_path, app.cfg.deploy_log)
    click.echo(json.dumps([dataclasses.asdict(e) for e in entries], default=str))


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
    """Count open vs acted annotations; with ``--root``, flag drifted quotes."""
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


def _find_entry(ledger_path: pathlib.Path, annotation_id: str) -> LedgerEntry:
    """The ledger entry whose ``id`` is ``annotation_id`` (first match)."""
    if not ledger_path.exists():
        raise click.ClickException(f"no ledger at {ledger_path}")
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = LedgerEntry.from_json(line)
        if entry.id == annotation_id:
            return entry
    raise click.ClickException(f"no ledger entry for id {annotation_id!r}")


@main.command()
@click.argument("annotation_id")
@click.pass_obj
def rewind(app: App, annotation_id: str) -> None:
    """Print the ``git checkout`` command restoring the doc as of the feedback."""
    click.echo(anchor.rewind(_find_entry(app.cfg.ledger_path, annotation_id)))


@main.command()
@click.argument("annotation_id")
@click.pass_obj
def delta(app: App, annotation_id: str) -> None:
    """Print the ``git diff`` of the annotated page since the feedback commit."""
    click.echo(anchor.delta(_find_entry(app.cfg.ledger_path, annotation_id)))
