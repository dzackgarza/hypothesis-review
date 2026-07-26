"""Append drained feedback to a git-tracked JSONL ledger in the ambient repo.

Every queue item an agent drains is recorded with its remediation explanation. The
ledger is a JSONL file at a repo-relative path (default :data:`DEFAULT_LEDGER`, or an
agent-named per-workflow file). A repeated annotation id is rejected rather than silently
discarding a second explanation.

There is no deploy-log or version anchoring: the ledger is committed, so git's own history
places each entry next to the code state it landed against, and an agent cross-references
from there. :func:`track` commits the ledger file only, with hooks bypassed -- feedback is
data, not a code change; it must not be blocked by the repo's commit gate and must record
while the repo is mid-edit.
"""

from __future__ import annotations

import pathlib
import subprocess

from annotate.models import Annotation, LedgerEntry

#: Canonical, repo-root-relative ledger used when the agent names none. Visible (not a
#: hidden dir) and repo-wide, so all feedback is findable and auditable in one place.
DEFAULT_LEDGER = pathlib.Path("feedback/ledger.jsonl")


def repo_root(cwd: pathlib.Path) -> pathlib.Path | None:
    """The git top-level of the repo containing ``cwd``, or ``None`` if ``cwd`` is not
    inside a git repository (the caller bounces on ``None`` — feedback must be tracked)."""
    result = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return pathlib.Path(result.stdout.strip())


def resolve(rel_path: pathlib.Path, root: pathlib.Path) -> pathlib.Path:
    """Absolute ledger path for a repo-relative ``rel_path``; refuse to escape ``root``."""
    root = root.resolve()
    abs_path = (root / rel_path).resolve()
    if abs_path != root and root not in abs_path.parents:
        raise ValueError(f"ledger path {rel_path} escapes the repo root {root}")
    return abs_path


def _recorded_ids(ledger_path: pathlib.Path) -> set[str]:
    """The ids already recorded, streamed line by line (the id set itself is the dedup
    contract and is held in memory; the file text is not)."""
    if not ledger_path.exists():
        return set()
    with ledger_path.open(encoding="utf-8") as lines:
        return {LedgerEntry.from_json(line).id for line in lines if line.strip()}


def append(
    annotations: list[Annotation],
    ledger_path: pathlib.Path,
    remediations: dict[str, str],
) -> list[LedgerEntry]:
    """Append one remediated JSONL line per annotation.

    ``created`` is stringified so the entry stays JSON-serializable. An annotation already
    present in the ledger is an error because accepting it would make two remediation
    explanations compete for ownership of the same queue item.
    """
    seen = _recorded_ids(ledger_path)
    duplicate_ids = [ann.id for ann in annotations if ann.id in seen]
    if duplicate_ids:
        raise ValueError(f"annotation(s) already drained into this ledger: {', '.join(duplicate_ids)}")
    entries = [
        LedgerEntry(
            id=ann.id,
            created=str(ann.created),
            uri=ann.uri,
            text=ann.text,
            tags=list(ann.tags),
            target=ann.target,
            quote=ann.quote,
            remediation=remediations[ann.id],
        )
        for ann in annotations
    ]
    if not entries:
        return []
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(entry.to_json() + "\n")
    return entries


def track(ledger_path: pathlib.Path, message: str) -> None:
    """``git add`` + commit the ledger file only, bypassing hooks.

    ``--no-verify`` is mandatory: the machine-wide ``core.hooksPath`` gate would otherwise
    run the code QC suite on this data commit. The ``-- <path>`` pathspec commits the ledger
    alone, leaving any in-progress work in the repo uncommitted.
    """
    d = str(ledger_path.parent)
    path = str(ledger_path)
    subprocess.run(
        ["git", "-C", d, "add", "--", path],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    subprocess.run(
        ["git", "-C", d, "commit", "--no-verify", "-m", message, "--", path],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
