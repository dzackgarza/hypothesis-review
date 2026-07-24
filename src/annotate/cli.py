"""annotate CLI.

The tool forces one thing: every batch of feedback an agent receives is recorded in a
git-tracked ledger. ``wait`` and ``pull`` record-then-print (they bounce unless run inside
a git repo — there is no unrecorded mode); ``slice`` is a read-only ad-hoc view that points
at ``record``; ``record`` appends chosen annotations. A session is a time window: ``wait``
parks the open timestamp locally and delivers the annotations created since it. Reads go
through :mod:`annotate.source`, the ``acted`` write through :mod:`annotate.api`; both are
injected via the click context object so tests swap in stubs.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import pathlib
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import click
import httpx
import psycopg

from annotate.api import HClient
from annotate.config import Config
from annotate.ledger import (
    DEFAULT_LEDGER,
)
from annotate.ledger import (
    append as ledger_append,
)
from annotate.ledger import (
    repo_root as ledger_repo_root,
)
from annotate.ledger import (
    resolve as ledger_resolve,
)
from annotate.ledger import (
    track as ledger_track,
)
from annotate.models import Annotation, LedgerEntry
from annotate.session import (
    ACTED,
    OPEN,
    SEND,
    batch_since,
    read_open_time,
    write_open_time,
)
from annotate.session_server import SESSION_CLOSE_PORT, wait_for_close
from annotate.source import AnnotationSource, PostgresSource


@dataclasses.dataclass
class App:
    """Wired dependencies stashed on ``ctx.obj``; tests inject a stub App."""

    source: AnnotationSource
    client: HClient
    group_id: str
    cfg: Config | None = None


def _now() -> datetime:
    """The current time on the clock h writes ``annotation.created`` on: UTC, naive.

    Every window this tool computes is compared against that column, so it has to be read
    from the same clock. Taking the local one instead moves every window by the machine's
    offset -- east of Greenwich that empties every session, silently, because notes written
    during it are stamped before it opened (hypothesis-review#16).
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _park(timeout: int | None, port: int, count_queued: Callable[[], int]) -> bool:
    """Block on the extension-facing loopback endpoints until closed, or time out.

    ``timeout`` is optional: ``None`` blocks until the browser closes the session (the
    default -- see ``wait``). ``count_queued`` answers the browser's status poll. It is
    evaluated per request, so the indicator in the toolbar tracks notes as they land rather
    than reporting the count at the moment the session opened.
    """
    return wait_for_close(timeout, port, count_queued)


def _current_batch(source: AnnotationSource, group_id: str, root: pathlib.Path) -> list[Annotation]:
    """The real annotations created since this repo's open session started.

    A missing open timestamp means no session was ever opened here: that is an error, not
    an empty delivery -- returning ``[]`` would let ``pull``/``resolve`` report success for
    a review window that never existed (hypothesis-review#7).
    """
    since = read_open_time(root)
    if since is None:
        raise click.ClickException("no review session is open in this repository -- run `annotate wait` (or open a session from the browser) before pulling or resolving")
    return batch_since(source.list(group_id), since)


def _anns_json(anns: list[Annotation]) -> str:
    return json.dumps([dataclasses.asdict(a) for a in anns], default=str)


def _ledger_path_option(f: Callable[..., Any]) -> Callable[..., Any]:
    """Shared ``--path`` option for the recording commands."""
    return click.option(
        "--path",
        "rel_path",
        default=None,
        help="Ledger file, relative to the repo root. Defaults to feedback/ledger.jsonl; name it per workflow (e.g. research-intake.jsonl). Created and tracked if absent.",
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


def _require_normalized(anns: list[Annotation], action: str) -> None:
    """Refuse to hand unnormalized quotes onward -- the fork's fail-loud contract."""
    missing = [ann for ann in anns if ann.normalization_error is not None]
    if missing:
        details = "; ".join(f"{ann.id}: {ann.normalization_error}" for ann in missing)
        raise click.ClickException(f"cannot {action} unnormalized annotation(s): {details}")


def _deliver(anns: list[Annotation], rel_path: str | None, client: HClient | None = None) -> None:
    """Record the delivered annotations, drain them from h, then print the batch.

    The order is the whole safety argument. Recording runs first, so feedback can never
    reach the agent unrecorded; draining runs only after that write returns, so a failed
    record leaves every note in the reader's sidebar rather than deleting the only copy.
    """
    _require_normalized(anns, "deliver")
    _record(anns, rel_path)
    if client is not None:
        for ann in anns:
            client.delete(ann.id)
    click.echo(_anns_json(anns))


@click.group()
@click.pass_context
def main(ctx: click.Context) -> None:
    """Git-anchored review loop over self-hosted Hypothesis annotations.

    Workflow: `wait` opens a review session (records the open timestamp locally) and blocks
    until the browser closes it; it then records the batch of annotations created during the
    window to a git-tracked ledger and prints it. You act on the feedback, then `resolve`
    tags it done. `slice` browses annotations made outside a session and `record` preserves
    the ones worth keeping.

    Recording is not optional: every command that hands feedback to an agent writes it to the
    ledger (default feedback/ledger.jsonl at the repo root) and commits it first, and refuses
    to run outside a git repo -- feedback stays auditable alongside the work it concerns. Run
    `annotate doctor` to check your setup.
    """
    # Diagnostics (e.g. the session-close server's request log) go to stderr; without a
    # handler the stdlib logger would silently drop them.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
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
    root = _require_repo()
    _deliver(_current_batch(app.source, app.group_id, root), rel_path)


@main.command()
@click.option(
    "--timeout",
    type=int,
    default=None,
    help=(
        "Optional seconds to wait for the browser's session-close request before giving "
        "up. Omitted by default: `wait` blocks until the browser closes the session, so "
        "the reader takes as long as they need. The agent running this owns the process "
        "and stops it by interrupting or killing it; set a bound only for automation."
    ),
)
@click.option(
    "--port",
    default=SESSION_CLOSE_PORT,
    show_default=True,
    help="Loopback port served for the browser's session-close request. Change it only alongside the extension, which posts to the declared port.",
)
@_ledger_path_option
@click.pass_obj
def wait(app: App, timeout: int | None, port: int, rel_path: str | None) -> None:
    """Open a session, block until the browser closes it, then record and print the batch JSON.

    Records the open timestamp locally (no h write), serves a loopback close endpoint, then
    delivers the real annotations created during the window when the extension calls it.

    The wait is unbounded by default: annotation is human work with no fixed length, and the
    agent that launched this owns the process -- it stops the wait by interrupting or killing
    it (the poll loop breaks on the signal promptly). ``--timeout`` sets an optional bound for
    automation, where an unclosed session should fail rather than hang.
    """
    root = _require_repo()  # bounce before opening a session whose batch we could not record
    write_open_time(root, _now())
    if not _park(timeout, port, lambda: len(_current_batch(app.source, app.group_id, root))):
        raise click.ClickException(f"timed out after {timeout}s waiting for the browser session-close request")
    _deliver(_current_batch(app.source, app.group_id, root), rel_path, app.client)


def _not_marker(ann: Annotation) -> bool:
    """A real annotation: not a leftover ``review:open``/``review:send`` legacy session marker."""
    return not (ann.is_marker(OPEN) or ann.is_marker(SEND))


_DURATION = re.compile(r"^(\d+)([smhd])$")
_UNIT = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}


def _parse_last(last: str) -> timedelta:
    """Parse a ``\\d+[smhd]`` relative window (e.g. ``2h``) into a timedelta."""
    m = _DURATION.match(last)
    if m is None:
        raise click.BadParameter(f"expected <int>[smhd], got {last!r}", param_hint="--last")
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
        since = _now() - _parse_last(last)
    anns = [a for a in app.source.list(app.group_id, since, until) if _not_marker(a)]
    if uri is not None:
        anns = [a for a in anns if a.uri == uri]
    click.echo(_anns_json(anns))
    if anns:
        click.echo(
            f"[annotate] {len(anns)} shown, not recorded. Preserve the relevant ones with:\n    annotate record {' '.join(a.id for a in anns)}",
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
    selected = [by_id[i] for i in annotation_ids]
    _require_normalized(selected, "record")
    new = _record(selected, rel_path)
    click.echo(json.dumps([dataclasses.asdict(e) for e in new], default=str))


@main.command()
@click.pass_obj
def resolve(app: App) -> None:
    """Tag the current batch's annotations `acted` via the h API."""
    root = _require_repo()
    anns = _current_batch(app.source, app.group_id, root)
    _require_normalized(anns, "resolve")
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
            if isinstance(sel, dict) and sel.get("type") == "TextQuoteSelector" and sel.get("exact"):
                quotes.append(sel["exact"])
    return quotes


#: Upper bound on how much built-site text drift detection will read into memory.
#: A tree past this size needs a uri->path map, not a bigger buffer.
_BUILD_TEXT_MAX_BYTES = 50 * 1024 * 1024


def _file_text(path: pathlib.Path) -> str:
    """The file's UTF-8 text, or a loud refusal naming it.

    The one place drift detection turns bytes into text, so it is the one place the answer
    could be silently falsified. A decoder asked to drop what it cannot decode deletes the
    offending bytes and splices their neighbours together, which both hides content the
    comparison should have seen and can manufacture text the tree does not contain -- and it
    does so without any signal, exactly where the command knows least about the tree. So the
    bytes are decoded strictly and a file that is not UTF-8 text stops the command with its
    path; nothing is skipped, replaced, or carried past. This is the sanctioned rendering of
    a structured decode failure into the CLI's error protocol: it terminates, and it neither
    defaults nor returns a partial answer.
    """
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise click.ClickException(
            f"{path}: byte {exc.start} is not valid UTF-8 ({exc.reason}). Drift detection compares every file under --root as text and will not derive a "
            "match/drift verdict from bytes it cannot decode. Point --root at the build's text output, or remove the non-text file."
        ) from exc


def _build_text(root: pathlib.Path) -> str:
    """Concatenated text of every file under ``root`` (the current build).

    Every regular file participates in the comparison -- that is the rule, not a consequence
    of which files happened to decode (see :func:`_file_text`).

    Bounded: the whole tree is read into memory for substring matching, so a tree
    larger than ``_BUILD_TEXT_MAX_BYTES`` is a loud error rather than a silent
    memory balloon.
    """
    parts: list[str] = []
    total = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        total += p.stat().st_size
        if total > _BUILD_TEXT_MAX_BYTES:
            raise click.ClickException(
                f"--root tree exceeds {_BUILD_TEXT_MAX_BYTES // (1024 * 1024)}MB; drift detection reads the whole tree into memory and needs a smaller build root"
            )
        parts.append(_file_text(p))
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


#: The status h's API root serves when the service is up. Readiness is stated positively:
#: this status is ready and every other one is not, so no status is accepted by omission.
_H_SERVING_STATUS = 200


def _h_serving(api_url: str) -> tuple[bool, str]:
    """Whether the configured h API is serving, and the detail to report.

    Answering is not serving. A proxy in front of a dead app answers 502, a misconfigured
    deployment answers 500: those are responses, and calling them readiness hands the
    operator a session that cannot work while claiming the environment was checked.
    """
    resp = httpx.get(f"{api_url.rstrip('/')}/api/", timeout=3.0)
    if resp.status_code == _H_SERVING_STATUS:
        return True, f"serving (HTTP {resp.status_code})"
    return False, f"answered HTTP {resp.status_code} -- responding, but not serving"


def _pg_serving(dsn: str) -> tuple[bool, str]:
    """Whether the configured Postgres is serving, and the detail to report. Connecting is
    not serving either: the readiness claim is that it answers a query with the right value."""
    with psycopg.connect(dsn, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        [(value,)] = cur.fetchall()
    return value == 1, f"serving (SELECT 1 -> {value})"


def _probe(label: str, thunk: Callable[[], tuple[bool, str]]) -> tuple[bool, str, str]:
    """Run a readiness probe and render its outcome as a status row.

    Three outcomes, kept apart: serving, answered-but-not-serving (the probe's own verdict),
    and no response at all. The last is the tool's one sanctioned error-to-status boundary --
    an absent dependency is a doctor result, not a crash -- and it is not allowed to absorb
    the middle one, because "the deployment is up and broken" and "the deployment is not
    there" are different problems for the operator to act on.
    """
    try:
        ready, detail = thunk()
    except (httpx.HTTPError, psycopg.Error, OSError) as exc:
        return False, label, f"no response: {(str(exc).splitlines() or [type(exc).__name__])[0]}"
    return ready, label, detail


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
        rows.append((False, "git repo", "not inside one -- no feedback can be recorded; cd into the repo you are reviewing, or run `git init`"))
    else:
        rows.append((True, "git repo", f"{root} (feedback -> {ledger_resolve(DEFAULT_LEDGER, root)})"))

    if cfg is None:
        rows.append((False, "config", "configuration was not loaded"))
    else:
        rows.append((True, "config", f"group {cfg.group_id}, api {cfg.api_url}"))
        rows.append(_probe("h API", lambda: _h_serving(cfg.api_url)))
        rows.append(_probe("Postgres", lambda: _pg_serving(cfg.pg_dsn)))

    for ok, label, detail in rows:
        click.echo(f"[{'OK' if ok else 'FAIL'}] {label}: {detail}")
    if not all(ok for ok, _, _ in rows):
        raise click.ClickException("not ready -- resolve the FAIL line(s) above")
