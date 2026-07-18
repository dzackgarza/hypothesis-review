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
        """Map an ``annotation`` table row to the h API annotation shape.

        h stores the page URL in ``target_uri`` and the selectors in
        ``target_selectors`` (the bare selector list); we reassemble the API
        ``target`` shape ``[{"source", "selector"}]`` the rest of the tool consumes.
        ``id`` is a Postgres ``uuid``; stringify it so the annotation stays
        JSON-serializable (the ledger dumps it without a ``uuid`` encoder).
        """
        return cls(
            id=str(row["id"]),
            created=row["created"],
            userid=row["userid"],
            group=row["groupid"],
            uri=row["target_uri"],
            text=row["text"],
            tags=list(row["tags"] or []),
            target=[{"source": row["target_uri"], "selector": row["target_selectors"]}],
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
