"""Present and drain the annotation queue owned by Hypothesis tags."""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Callable
from typing import Any

import click
import httpx
import psycopg

from annotate.api import HClient
from annotate.config import Config
from annotate.ledger import DEFAULT_LEDGER
from annotate.ledger import append as ledger_append
from annotate.ledger import repo_root as ledger_repo_root
from annotate.ledger import resolve as ledger_resolve
from annotate.ledger import track as ledger_track
from annotate.models import Annotation, LedgerEntry
from annotate.source import AnnotationSource, PostgresSource

#: Native Hypothesis tag which marks an annotation as pending agent work. It is
#: deliberately visible in the ordinary tag UI: the queue must never be hidden
#: behind extension-only state.
AGENT_QUEUE = "agent:queue"


@dataclasses.dataclass
class App:
    """Wired dependencies stashed on ``ctx.obj``."""

    source: AnnotationSource
    client: HClient
    group_id: str
    cfg: Config | None = None


def _anns_json(annotations: list[Annotation]) -> str:
    return json.dumps(
        [dataclasses.asdict(annotation) for annotation in annotations],
        default=str,
    )


def _active_queue(app: App) -> list[Annotation]:
    """Queued annotations in creation order."""
    return [annotation for annotation in app.source.list(app.group_id) if AGENT_QUEUE in annotation.tags]


def _require_repo() -> pathlib.Path:
    root = ledger_repo_root(pathlib.Path.cwd())
    if root is None:
        raise click.ClickException("not inside a git repository; drained feedback must be recorded alongside the work it changed")
    return root


def _require_normalized(annotations: list[Annotation]) -> None:
    missing = [annotation for annotation in annotations if annotation.normalization_error is not None]
    if missing:
        details = "; ".join(f"{annotation.id}: {annotation.normalization_error}" for annotation in missing)
        raise click.ClickException(f"cannot drain unnormalized annotation(s): {details}")


def _ledger_path_option(function: Callable[..., Any]) -> Callable[..., Any]:
    return click.option(
        "--path",
        "rel_path",
        default=None,
        help=("Ledger file relative to the reviewed repository. Defaults to feedback/ledger.jsonl."),
    )(function)


def _drain(
    app: App,
    rel_path: str | None,
    items: tuple[tuple[str, str], ...],
) -> list[LedgerEntry]:
    """Commit the whole requested batch to the ledger, then delete it from h."""
    root = _require_repo()
    queue_by_id = {annotation.id: annotation for annotation in _active_queue(app)}
    requested_ids = [annotation_id for annotation_id, _ in items]
    missing = [annotation_id for annotation_id in requested_ids if annotation_id not in queue_by_id]
    if missing:
        raise click.ClickException(f"annotation id(s) not in the active queue: {', '.join(missing)}")
    blank = [annotation_id for annotation_id, remediation in items if remediation.strip() == ""]
    if blank:
        raise click.ClickException(f"remediation explanation is blank for: {', '.join(blank)}")

    selected = [queue_by_id[annotation_id] for annotation_id in requested_ids]
    _require_normalized(selected)
    ledger_path = ledger_resolve(
        pathlib.Path(rel_path) if rel_path else DEFAULT_LEDGER,
        root,
    )
    try:
        entries = ledger_append(selected, ledger_path, dict(items))
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    ledger_track(
        ledger_path,
        f"feedback: drain {len(entries)} annotation(s) into {ledger_path.relative_to(root)}",
    )
    for annotation in selected:
        app.client.delete(annotation.id)
    return entries


@click.group(invoke_without_command=True, no_args_is_help=False)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Show the active annotation queue, or drain remediated items from it."""
    if ctx.obj is None:
        cfg = Config.load()
        ctx.obj = App(
            source=PostgresSource(cfg.pg_dsn),
            client=HClient(cfg.api_url, cfg.token),
            group_id=cfg.group_id,
            cfg=cfg,
        )
    if ctx.invoked_subcommand is None:
        click.echo(_anns_json(_active_queue(ctx.obj)))


@main.command()
@click.pass_obj
def queue(app: App) -> None:
    """Print the active queue as JSON."""
    click.echo(_anns_json(_active_queue(app)))


@main.command()
@click.option(
    "--item",
    "items",
    type=(str, str),
    multiple=True,
    required=True,
    metavar="ANNOTATION_ID REMEDIATION",
    help=("Drain one annotation with the explanation of the remediation applied. Repeat --item to drain a batch."),
)
@_ledger_path_option
@click.pass_obj
def drain(
    app: App,
    rel_path: str | None,
    items: tuple[tuple[str, str], ...],
) -> None:
    """Record remediations, then replace each queue flag with ``acted``."""
    entries = _drain(app, rel_path, items)
    click.echo(
        json.dumps(
            [dataclasses.asdict(entry) for entry in entries],
            default=str,
        )
    )


_H_SERVING_STATUS = 200


def _h_serving(api_url: str) -> tuple[bool, str]:
    response = httpx.get(f"{api_url.rstrip('/')}/api/", timeout=3.0)
    if response.status_code == _H_SERVING_STATUS:
        return True, f"serving (HTTP {response.status_code})"
    return False, f"answered HTTP {response.status_code} -- responding, but not serving"


def _pg_serving(dsn: str) -> tuple[bool, str]:
    with psycopg.connect(dsn, connect_timeout=3) as connection:
        [(value,)] = connection.execute("SELECT 1").fetchall()
    return value == 1, f"serving (SELECT 1 -> {value})"


def _probe(
    label: str,
    operation: Callable[[], tuple[bool, str]],
) -> tuple[bool, str, str]:
    try:
        ready, detail = operation()
    except (httpx.HTTPError, psycopg.Error, OSError) as error:
        first_line = (str(error).splitlines() or [type(error).__name__])[0]
        return False, label, f"no response: {first_line}"
    return ready, label, detail


@main.command()
@click.pass_obj
def doctor(app: App) -> None:
    """Check the git, configuration, API, and database boundaries."""
    cfg = app.cfg
    rows: list[tuple[bool, str, str]] = []
    root = ledger_repo_root(pathlib.Path.cwd())
    rows.append(
        (
            root is not None,
            "git repo",
            str(root) if root is not None else "not inside one",
        )
    )
    if cfg is None:
        rows.append((False, "config", "configuration was not loaded"))
    else:
        rows.append((True, "config", f"group {cfg.group_id}, api {cfg.api_url}"))
        rows.append(_probe("h API", lambda: _h_serving(cfg.api_url)))
        rows.append(_probe("Postgres", lambda: _pg_serving(cfg.pg_dsn)))
    for ready, label, detail in rows:
        click.echo(f"[{'OK' if ready else 'FAIL'}] {label}: {detail}")
    if not all(ready for ready, _, _ in rows):
        raise click.ClickException("not ready -- resolve the FAIL line(s) above")
