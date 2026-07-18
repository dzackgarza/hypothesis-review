"""The git-anchored JSONL ledger, written into the *reviewed* repo.

:func:`append` tees a :class:`~annotate.models.Batch` to the ledger — one
:class:`~annotate.models.LedgerEntry` per annotation, each stamped with the
commit that was live when it was written (via
:func:`~annotate.anchor.commit_at` against the reviewed repo's deploy-log).
Append-only; entries start ``state="open"``.
"""

from __future__ import annotations

import pathlib

from annotate.anchor import commit_at
from annotate.models import Batch, LedgerEntry


def append(
    batch: Batch, ledger_path: pathlib.Path, deploy_log: pathlib.Path
) -> list[LedgerEntry]:
    """Append one JSONL line per batch annotation to ``ledger_path`` and return
    the written entries (each ``commit``-stamped, ``state="open"``)."""
    entries = [
        LedgerEntry(
            id=ann.id,
            created=ann.created,
            uri=ann.uri,
            text=ann.text,
            tags=list(ann.tags),
            target=ann.target,
            commit=commit_at(ann.created, deploy_log),
            state="open",
        )
        for ann in batch.annotations
    ]
    with ledger_path.open("a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(entry.to_json() + "\n")
    return entries
