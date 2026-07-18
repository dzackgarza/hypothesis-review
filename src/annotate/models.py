"""annotate data models.

A *marker* is just an :class:`Annotation` carrying a ``review:open`` /
``review:send`` tag (see :meth:`Annotation.is_marker`); there is no separate
Marker type. A :class:`Batch` is the annotation window between an open and a
send marker. A :class:`LedgerEntry` is one git-anchored, JSON-serializable line
of the reviewed repo's JSONL ledger.
"""

from __future__ import annotations

import base64
import dataclasses
import json
import uuid
from dataclasses import dataclass, field
from typing import Any


def _public_id(raw: Any) -> str:
    """h's public annotation id: url-safe base64 of the uuid bytes, no padding.

    The DB stores ``id`` as a Postgres ``uuid``; the h API addresses annotations by
    this flattened form (e.g. ``PATCH /api/annotations/<public-id>``), so reads must
    surface it — otherwise write-back calls (``resolve``) 404 with the raw uuid.
    """
    u = raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
    return base64.urlsafe_b64encode(u.bytes).rstrip(b"=").decode()


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
        ``id`` is the Postgres ``uuid`` flattened to h's public id, so it matches the
        API and is JSON-serializable (the ledger dumps without a ``uuid`` encoder).
        """
        return cls(
            id=_public_id(row["id"]),
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
