"""Real-boundary tests for :class:`HClient`.

HClient's job is to talk to the live h API correctly: create markers and merge tags.
A mocked transport accepts any request, so it cannot catch a wrong endpoint, a
malformed body, or a bad id format -- which is exactly how the uuid-vs-public-id bug
reached the tag path. These drive the live API and read the result back through
Postgres, then clean up. They require the live h stack (the tool's real boundary), so
its absence fails loudly rather than skipping.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from annotate.api import MARKER_URI, HClient
from annotate.config import Config


def _seed(cfg: Config, tags: list[str]) -> str:
    """Create an annotation via the live API; return its h public id."""
    uri = f"http://example.test/{uuid.uuid4().hex}"
    resp = httpx.post(
        f"{cfg.api_url}/api/annotations",
        headers={"Authorization": f"Bearer {cfg.token}"},
        json={"uri": uri, "text": "note", "tags": tags, "group": cfg.group_id},
    )
    resp.raise_for_status()
    return resp.json()["id"]


def _rows_for_tag(dsn: str, tag: str) -> list[dict[str, Any]]:
    with psycopg.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT target_uri, tags, groupid FROM annotation "
            "WHERE tags @> ARRAY[%s]::text[] AND deleted = false",
            (tag,),
        )
        return cur.fetchall()


@pytest.fixture
def run() -> Any:
    """A per-run unique tag every created annotation carries, hard-deleted after."""
    cfg = Config.load()
    tag = f"__it_{uuid.uuid4().hex}"
    yield cfg, tag
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM annotation WHERE tags @> ARRAY[%s]::text[]", (tag,))
        conn.commit()


@pytest.mark.pg
def test_create_marker_lands_in_group_tagged_with_its_kind(run: Any) -> None:
    cfg, kind = run  # the marker "kind" doubles as this run's cleanup tag
    new_id = HClient(cfg.api_url, cfg.token).create_marker(cfg.group_id, kind)

    assert new_id  # server assigned a public id
    rows = _rows_for_tag(cfg.pg_dsn, kind)
    assert len(rows) == 1
    assert rows[0]["target_uri"] == MARKER_URI  # markers anchor on the synthetic uri
    assert rows[0]["groupid"] == cfg.group_id
    assert rows[0]["tags"] == [kind]


@pytest.mark.pg
def test_tag_adds_new_tag_and_merges_existing_without_duplicating(run: Any) -> None:
    cfg, tag = run
    api_id = _seed(cfg, [tag, "paperA"])  # annotation already carries paperA

    HClient(cfg.api_url, cfg.token).tag(api_id, ["acted", "paperA"])

    rows = _rows_for_tag(cfg.pg_dsn, tag)
    assert len(rows) == 1
    tags = rows[0]["tags"]
    assert set(tags) == {tag, "paperA", "acted"}  # acted added; nothing dropped
    assert len(tags) == len(set(tags))  # paperA merged, not duplicated
