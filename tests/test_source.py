"""Real-boundary tests for :class:`PostgresSource`.

The read path's whole job is to turn real h ``annotation`` rows into
:class:`~annotate.models.Annotation` objects. Proving that requires executing the
real query against the real schema — a SQL-string assertion would pass even when the
query names columns that do not exist (which is exactly the bug these tests now
catch). So each test seeds annotations through the **live h API** (the same path the
browser extension uses — an independent oracle for how h populates the columns), then
reads them back through Postgres and asserts the mapping.

These require the live h stack (API + Postgres), which is the tool's real boundary:
if it is absent the tool cannot function, so the tests fail loudly rather than skip.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import psycopg
import pytest

from annotate.config import Config
from annotate.source import PostgresSource


def _seed(cfg: Config, uri: str, text: str, tags: list[str], selectors: Any = None) -> str:
    """Create one annotation via the real h API (as the extension does); return the
    h public (API) id the server assigned — the id downstream API calls must use."""
    payload: dict[str, Any] = {"uri": uri, "text": text, "tags": tags, "group": cfg.group_id}
    if selectors is not None:
        payload["target"] = [{"source": uri, "selector": selectors}]
    resp = httpx.post(
        f"{cfg.api_url}/api/annotations",
        headers={"Authorization": f"Bearer {cfg.token}"},
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()["id"]


@pytest.fixture
def seeded() -> Any:
    """Two annotations in creation order in the configured group, tagged with a
    per-run unique marker for isolation; hard-deleted from Postgres afterward."""
    cfg = Config.load()
    tag = f"__annotate_it_{uuid.uuid4().hex}"
    page = f"http://example.test/{uuid.uuid4().hex}"
    quote = [{"type": "TextQuoteSelector", "exact": "hello world"}]
    id1 = _seed(cfg, page, "first note", [tag], selectors=quote)
    id2 = _seed(cfg, page, "second note", [tag])  # no selectors -> target_selectors defaults to []
    yield cfg, tag, page, [id1, id2]
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM annotation WHERE tags @> ARRAY[%s]::text[]", (tag,))
        conn.commit()


def _run_rows(cfg: Config, tag: str, **kw: Any) -> list:
    """PostgresSource rows in the configured group carrying this run's tag."""
    return [a for a in PostgresSource(cfg.pg_dsn).list(cfg.group_id, **kw) if tag in a.tags]


@pytest.mark.pg
def test_list_maps_real_columns_in_created_order(seeded: Any) -> None:
    cfg, tag, page, api_ids = seeded
    rows = _run_rows(cfg, tag)

    assert [a.text for a in rows] == ["first note", "second note"]  # ORDER BY created
    assert [a.id for a in rows] == api_ids  # uuid column -> h public (API) id
    assert all(a.uri == page for a in rows)  # target_uri -> uri
    assert all(a.group == cfg.group_id for a in rows)  # groupid -> group
    # target_selectors -> reconstructed h API target shape (what _exact_quotes consumes)
    assert rows[0].target == [
        {"source": page, "selector": [{"type": "TextQuoteSelector", "exact": "hello world"}]}
    ]
    assert rows[1].target == [{"source": page, "selector": []}]


@pytest.mark.pg
def test_list_since_is_exclusive_and_until_is_inclusive(seeded: Any) -> None:
    cfg, tag, _page, _ids = seeded
    first, second = _run_rows(cfg, tag)

    # since is exclusive: drops the row created at exactly `first.created`.
    assert [a.text for a in _run_rows(cfg, tag, since=first.created)] == ["second note"]
    # until is inclusive: keeps the row created at exactly `first.created`.
    assert [a.text for a in _run_rows(cfg, tag, until=first.created)] == ["first note"]
    assert second.created > first.created  # sanity: distinct, ordered timestamps
