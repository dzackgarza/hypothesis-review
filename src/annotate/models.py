"""annotate data models.

A *marker* is just an :class:`Annotation` carrying a ``review:open`` /
``review:send`` tag (see :meth:`Annotation.is_marker`); there is no separate
Marker type. A :class:`Batch` is the annotation window between an open and a
send marker. A :class:`LedgerEntry` is one git-anchored, JSON-serializable line
of the reviewed repo's JSONL ledger.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Annotation:
    id: str
    created: Any
    userid: str
    group: str
    uri: str
    text: str
    tags: list[str] = field(default_factory=list)
    target: Any = None

    def is_marker(self, kind: str) -> bool:
        return kind in self.tags

    @classmethod
    def from_pg_row(cls, row: dict[str, Any]) -> Annotation:
        """Map an ``annotation`` table row (``groupid`` → ``group``); ``target``
        (h selector JSON) and ``tags`` pass through verbatim."""
        return cls(
            id=row["id"],
            created=row["created"],
            userid=row["userid"],
            group=row["groupid"],
            uri=row["uri"],
            text=row["text"],
            tags=list(row["tags"] or []),
            target=row["target"],
        )


@dataclass(frozen=True)
class Batch:
    open_marker: Annotation
    send_marker: Annotation
    annotations: list[Annotation]


@dataclass(frozen=True)
class LedgerEntry:
    id: str
    created: str
    uri: str
    text: str
    tags: list[str]
    target: Any
    commit: str
    state: str

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self))

    @classmethod
    def from_json(cls, data: str) -> LedgerEntry:
        return cls(**json.loads(data))
