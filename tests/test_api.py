"""Real-boundary tests for :class:`HClient`.

HClient's one job is to talk to the live h API correctly: merge the ``acted`` tag onto a
resolved annotation. A mocked transport accepts any request, so it cannot catch a wrong
endpoint, a malformed body, or a bad id format -- which is exactly how the uuid-vs-public-id
bug reached the tag path. This drives the live API and reads the result back through
Postgres, then cleans up. It requires the live h stack (the tool's real boundary), so its
absence fails loudly rather than skipping.

Seeds go through the real create path, which now normalizes the quote synchronously at
intake and rejects any quote-less create. So the seeded annotation carries a
``TextQuoteSelector`` whose ``exact`` spans a ``<span class="math">`` on the frameworkmath
render-test page: h recovers its LaTeX from the page source (no Mathpix) and returns 200.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from annotate.api import HClient
from annotate.config import Config

# A recoverable selection on the frameworkmath page: the flattened MathJax capture of the
# inline math span, which h's Node/KaTeX extractor maps back to authored LaTeX at intake.
FRAMEWORK_URL = "http://localhost:3012/document/frameworkmath"
RECOVERABLE_EXACT = "and ιB:C.B→C their intersection"


def _seed(cfg: Config, tags: list[str]) -> str:
    """Create an annotation via the live API; return its h public id. Carries a recoverable
    math quote so the synchronous intake normalization accepts it (200)."""
    resp = httpx.post(
        f"{cfg.api_url}/api/annotations",
        headers={"Authorization": f"Bearer {cfg.token}"},
        json={
            "uri": FRAMEWORK_URL,
            "text": "note",
            "tags": tags,
            "group": cfg.group_id,
            "target": [
                {
                    "source": FRAMEWORK_URL,
                    "selector": [{"type": "TextQuoteSelector", "exact": RECOVERABLE_EXACT}],
                }
            ],
        },
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
def test_tag_adds_new_tag_and_merges_existing_without_duplicating(run: Any) -> None:
    cfg, tag = run
    api_id = _seed(cfg, [tag, "paperA"])  # annotation already carries paperA

    HClient(cfg.api_url, cfg.token).tag(api_id, ["acted", "paperA"])

    rows = _rows_for_tag(cfg.pg_dsn, tag)
    assert len(rows) == 1
    tags = rows[0]["tags"]
    assert set(tags) == {tag, "paperA", "acted"}  # acted added; nothing dropped
    assert len(tags) == len(set(tags))  # paperA merged, not duplicated
