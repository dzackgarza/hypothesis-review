"""Append web feedback to a git-tracked JSONL ledger in the ambient repo.

A ledger is a JSONL file the agent names for the workflow (``research-intake.jsonl``,
``book-updates.jsonl``, ``app-feedback.jsonl``) at a path inside the repo being worked
on. There is no deploy-log or version anchoring: the ledger is committed, so git's own
history places each entry next to the code state it landed against, and an agent
cross-references from there.

:func:`track` commits the ledger file only, with hooks bypassed -- feedback capture is
data, not a code change; it must not run (or be blocked by) the repo's commit gate, and
must record while the repo is mid-edit.
"""

from __future__ import annotations

import pathlib
import subprocess

from annotate.models import Batch, LedgerEntry


def repo_root(cwd: pathlib.Path) -> pathlib.Path:
    """The git top-level of the repo containing ``cwd`` (raises if ``cwd`` is not
    inside a git repository)."""
    out = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        check=True, capture_output=True, text=True,
    )
    return pathlib.Path(out.stdout.strip())


def resolve(rel_path: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    """Absolute ledger path for a repo-relative ``rel_path``; refuse to escape ``root``."""
    root = root.resolve()
    abs_path = (root / rel_path).resolve()
    if abs_path != root and root not in abs_path.parents:
        raise ValueError(f"ledger path {rel_path} escapes the repo root {root}")
    return abs_path


def append(batch: Batch, ledger_path: pathlib.Path) -> list[LedgerEntry]:
    """Append one JSONL line per batch annotation to ``ledger_path``; return the
    entries. ``created`` is stringified so the entry stays JSON-serializable."""
    entries = [
        LedgerEntry(
            id=ann.id,
            created=str(ann.created),
            uri=ann.uri,
            text=ann.text,
            tags=list(ann.tags),
            target=ann.target,
        )
        for ann in batch.annotations
    ]
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(entry.to_json() + "\n")
    return entries


def track(ledger_path: pathlib.Path, message: str) -> None:
    """``git add`` + commit the ledger file only, bypassing hooks.

    ``--no-verify`` is mandatory: the machine-wide ``core.hooksPath`` gate would
    otherwise run the code QC suite on this data commit. The ``-- <path>`` pathspec
    commits the ledger alone, leaving any in-progress work in the repo uncommitted.
    """
    d = str(ledger_path.parent)
    path = str(ledger_path)
    subprocess.run(["git", "-C", d, "add", "--", path], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", d, "commit", "--no-verify", "-m", message, "--", path],
        check=True, capture_output=True, text=True,
    )
