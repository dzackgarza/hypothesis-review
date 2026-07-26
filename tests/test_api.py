"""Real-boundary tests for :class:`HClient`.

HClient's one job is to talk to the live h API correctly: delete a resolved annotation.
A mocked transport accepts any request, so it cannot catch a wrong
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
from collections.abc import Iterator
from typing import Any

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

from annotate.api import HClient
from annotate.config import Config

# A recoverable selection on the frameworkmath page: the flattened MathJax capture of the
# inline math span, which h's Node/KaTeX extractor maps back to authored LaTeX at intake.
RECOVERABLE_EXACT = "and ιB:C.B→C their intersection"


def _seed(cfg: Config, tags: list[str], page_url: str) -> str:
    """Create an annotation via the live API; return its h public id. Carries a recoverable
    math quote (on ``page_url``, the test-served fixture page) so the synchronous intake
    normalization accepts it (200)."""
    resp = httpx.post(
        f"{cfg.api_url}/api/annotations",
        headers={"Authorization": f"Bearer {cfg.token}"},
        json={
            "uri": page_url,
            "text": "note",
            "tags": tags,
            "group": cfg.group_id,
            "target": [
                {
                    "source": page_url,
                    "selector": [{"type": "TextQuoteSelector", "exact": RECOVERABLE_EXACT}],
                }
            ],
        },
    )
    resp.raise_for_status()
    annotation_id = resp.json()["id"]
    assert isinstance(annotation_id, str)
    return annotation_id


def _rows_for_tag(dsn: str, tag: str) -> list[dict[str, Any]]:
    with psycopg.connect(dsn) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT target_uri, tags, groupid FROM annotation WHERE tags @> ARRAY[%s]::text[] AND deleted = false",
            (tag,),
        )
        return cur.fetchall()


@pytest.fixture
def run(framework_page: str) -> Iterator[tuple[Config, str, str]]:
    """A per-run unique tag every created annotation carries, hard-deleted after; plus the
    URL of the test-served fixture page the seeds annotate."""
    cfg = Config.load()
    tag = f"__it_{uuid.uuid4().hex}"
    yield cfg, tag, framework_page
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM annotation WHERE tags @> ARRAY[%s]::text[]", (tag,))
        conn.commit()


@pytest.mark.pg
def test_delete_removes_annotation(run: Any) -> None:
    cfg, tag, page_url = run
    api_id = _seed(cfg, [tag, "paperA", "agent:queue"], page_url)

    HClient(cfg.api_url, cfg.token).delete(api_id)

    assert _rows_for_tag(cfg.pg_dsn, tag) == []
