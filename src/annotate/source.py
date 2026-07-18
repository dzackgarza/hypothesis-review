"""Annotation sources: the read side of the loop.

Reads never go through ``/api/search`` (observed ES-index gap); :class:`PostgresSource`
reads the ``annotation`` table directly. Anything satisfying :class:`AnnotationSource`
(e.g. a test stub) is interchangeable with it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from annotate.models import Annotation


class AnnotationSource(Protocol):
    def list(
        self,
        group_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Annotation]:
        """Annotations in ``group_id``, ``created`` ascending, markers included.

        ``since`` is exclusive (``created > since``); ``until`` is inclusive
        (``created <= until``).
        """
        ...


def _build_query(
    group_id: str, since: datetime | None, until: datetime | None
) -> tuple[sql.Composed, list[Any]]:
    """Assemble the parameterized SELECT. All user data flows through ``params``;
    the SQL is composed only from constant fragments (psycopg ``sql.SQL``)."""
    clauses = [sql.SQL("groupid = %s"), sql.SQL("deleted = false")]
    params: list[Any] = [group_id]
    if since is not None:
        clauses.append(sql.SQL("created > %s"))
        params.append(since)
    if until is not None:
        clauses.append(sql.SQL("created <= %s"))
        params.append(until)
    query = sql.SQL(
        "SELECT id, created, userid, groupid, target_uri, target_selectors, text, tags "
        "FROM annotation WHERE {where} ORDER BY created"
    ).format(where=sql.SQL(" AND ").join(clauses))
    return query, params


class PostgresSource:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def list(
        self,
        group_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Annotation]:
        query, params = _build_query(group_id, since, until)
        with psycopg.connect(self._dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(query, params)
            rows = cur.fetchall()
        # psycopg adapts pg text[] -> list and jsonb -> Python natively.
        return [Annotation.from_pg_row(row) for row in rows]
