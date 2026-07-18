import os
import uuid
from datetime import datetime, timezone

import psycopg
import pytest
from psycopg.types.json import Json

from annotate.models import Annotation
from annotate.source import AnnotationSource, PostgresSource, _build_query


def _ann(text: str, created: datetime, group: str = "grp", tags: list[str] | None = None) -> Annotation:
    return Annotation(
        id=uuid.uuid4().hex,
        created=created,
        userid="acct:me@localhost",
        group=group,
        uri=f"http://example/{text}",
        text=text,
        tags=tags or [],
        target=None,
    )


class _StubSource:
    """In-memory :class:`AnnotationSource` implementing the interface contract."""

    def __init__(self, anns: list[Annotation]) -> None:
        self._anns = anns

    def list(
        self,
        group_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Annotation]:
        rows = [a for a in self._anns if a.group == group_id]
        if since is not None:
            rows = [a for a in rows if a.created > since]
        if until is not None:
            rows = [a for a in rows if a.created <= until]
        return sorted(rows, key=lambda a: a.created)


def _dt(day: int) -> datetime:
    return datetime(2024, 1, day, tzinfo=timezone.utc)


# --- interface contract (always-run, stub source) ---------------------------


def test_list_orders_by_created_and_includes_markers():
    marker = _ann("open", _dt(1), tags=["review:open"])
    src: AnnotationSource = _StubSource([_ann("c", _dt(3)), marker, _ann("b", _dt(2))])
    got = src.list("grp")
    assert [a.text for a in got] == ["open", "b", "c"]  # created ascending
    assert got[0].is_marker("review:open")  # markers are not filtered out


def test_since_is_exclusive_and_until_is_inclusive():
    src = _StubSource([_ann("a", _dt(1)), _ann("b", _dt(2)), _ann("c", _dt(3))])
    # since=_dt(1) drops the equal-created "a"; until=_dt(2) keeps the equal "b".
    assert [a.text for a in src.list("grp", since=_dt(1), until=_dt(2))] == ["b"]


def test_list_filters_by_group():
    src = _StubSource([_ann("a", _dt(1), group="grp"), _ann("x", _dt(1), group="other")])
    assert [a.text for a in src.list("grp")] == ["a"]


# --- PostgresSource query assembly (always-run, no DB) ----------------------


def test_build_query_base_has_group_and_deleted_filter():
    query, params = _build_query("g1", None, None)
    assert query.as_string(None) == (
        "SELECT id, created, userid, groupid, uri, text, tags, target "
        "FROM annotation WHERE groupid = %s AND deleted = false ORDER BY created"
    )
    assert params == ["g1"]


def test_build_query_adds_since_and_until_bounds_in_order():
    query, params = _build_query("g1", _dt(1), _dt(9))
    text = query.as_string(None)
    assert "created > %s" in text
    assert "created <= %s" in text
    assert params == ["g1", _dt(1), _dt(9)]
    assert text.endswith("ORDER BY created")


# --- real Postgres (opt-in) -------------------------------------------------


@pytest.mark.pg
@pytest.mark.skipif(
    os.environ.get("ANNOTATE_PG_IT") != "1",
    reason="live h Postgres integration; set ANNOTATE_PG_IT=1",
)
def test_postgres_source_lists_rows_ordered():
    dsn = os.environ.get("ANNOTATE_PG_DSN", "postgresql://postgres@127.0.0.1:5432/postgres")
    group = f"__annotate_it_{uuid.uuid4().hex}"
    # ponytail: relies on h annotation defaults for columns beyond these; adjust the
    # INSERT if the live schema requires more NOT NULL columns.
    rows = [
        (uuid.uuid4(), _dt(2), "later", ["review:x"]),
        (uuid.uuid4(), _dt(1), "earlier", []),
    ]
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                for aid, created, text, tags in rows:
                    cur.execute(
                        "INSERT INTO annotation "
                        "(id, created, updated, userid, groupid, uri, text, tags, target, deleted) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, false)",
                        (aid, created, created, "acct:it@localhost", group,
                         f"http://example/{text}", text, tags, Json({})),
                    )
            conn.commit()

        got = PostgresSource(dsn).list(group)
        assert [a.text for a in got] == ["earlier", "later"]  # created ascending
        assert got[0].is_marker("review:x") is False
        assert got[1].is_marker("review:x") is True
    finally:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM annotation WHERE groupid = %s", (group,))
            conn.commit()
