"""Real-boundary tests for :class:`PostgresSource`.

The read path's whole job is to turn real h ``annotation`` rows into
:class:`~annotate.models.Annotation` objects. Proving that requires executing the
real query against the real schema — a SQL-string assertion would pass even when the
query names columns that do not exist (which is exactly the bug these tests now
catch). So each test seeds annotations through the **live h API** (the same path the
browser extension uses — an independent oracle for how h populates the columns), then
reads them back through Postgres and asserts the mapping.

The live create now normalizes the quote synchronously at intake and rejects any quote-less
create, so every seed carries a ``TextQuoteSelector`` whose ``exact`` spans a
``<span class="math">`` on the frameworkmath render-test page: h recovers the LaTeX from the
page source (no Mathpix) and returns 200.

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

FRAMEWORK_URL = "http://localhost:3012/document/frameworkmath"
# Two distinct recoverable selections on the frameworkmath page (flattened MathJax captures
# that h's Node/KaTeX extractor maps back to authored LaTeX at intake).
EXACT_1 = "and ιB:C.B→C their intersection"
EXACT_2 = "pullback in Cat"


def _seed(
    cfg: Config,
    text: str,
    tags: list[str],
    exact: str,
    prefix: str | None = None,
    suffix: str | None = None,
) -> str:
    """Create one annotation via the real h API (as the extension does) with a recoverable
    math quote; return the h public (API) id the server assigned."""
    selector: dict[str, Any] = {"type": "TextQuoteSelector", "exact": exact}
    if prefix is not None:
        selector["prefix"] = prefix
    if suffix is not None:
        selector["suffix"] = suffix
    resp = httpx.post(
        f"{cfg.api_url}/api/annotations",
        headers={"Authorization": f"Bearer {cfg.token}"},
        json={
            "uri": FRAMEWORK_URL,
            "text": text,
            "tags": tags,
            "group": cfg.group_id,
            "target": [{"source": FRAMEWORK_URL, "selector": [selector]}],
        },
    )
    resp.raise_for_status()
    return resp.json()["id"]


@pytest.fixture
def seeded() -> Any:
    """Two annotations in creation order in the configured group, each carrying a distinct
    recoverable math quote, tagged with a per-run unique marker for isolation; hard-deleted
    from Postgres afterward."""
    cfg = Config.load()
    tag = f"__annotate_it_{uuid.uuid4().hex}"
    id1 = _seed(cfg, "first note", [tag], EXACT_1)
    id2 = _seed(cfg, "second note", [tag], EXACT_2)
    yield cfg, tag, FRAMEWORK_URL, [id1, id2]
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
    assert rows[0].quote == EXACT_1  # TextQuoteSelector.exact -> quote (stored verbatim)
    assert rows[1].quote == EXACT_2
    assert all(a.uri == page for a in rows)  # target_uri -> uri
    assert all(a.group == cfg.group_id for a in rows)  # groupid -> group
    # target_selectors -> reconstructed h API target shape (what _exact_quotes consumes)
    assert rows[0].target == [
        {"source": page, "selector": [{"type": "TextQuoteSelector", "exact": EXACT_1}]}
    ]
    assert rows[1].target == [
        {"source": page, "selector": [{"type": "TextQuoteSelector", "exact": EXACT_2}]}
    ]


@pytest.mark.pg
def test_list_since_is_exclusive_and_until_is_inclusive(seeded: Any) -> None:
    cfg, tag, _page, _ids = seeded
    first, second = _run_rows(cfg, tag)

    # since is exclusive: drops the row created at exactly `first.created`.
    assert [a.text for a in _run_rows(cfg, tag, since=first.created)] == ["second note"]
    # until is inclusive: keeps the row created at exactly `first.created`.
    assert [a.text for a in _run_rows(cfg, tag, until=first.created)] == ["first note"]
    assert second.created > first.created  # sanity: distinct, ordered timestamps


@pytest.fixture
def seeded_with_prose_context() -> Any:
    """One annotation whose TextQuoteSelector carries prefix/suffix prose anchors alongside the
    recoverable exact — the surrounding context h stores for anchoring. Hard-deleted afterward."""
    cfg = Config.load()
    tag = f"__annotate_it_{uuid.uuid4().hex}"
    _seed(
        cfg,
        "context note",
        [tag],
        EXACT_1,
        prefix="For classifiers ",
        suffix=" is the pullback",
    )
    yield cfg, tag
    with psycopg.connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM annotation WHERE tags @> ARRAY[%s]::text[]", (tag,))
        conn.commit()


@pytest.mark.pg
def test_from_pg_row_surfaces_prose_context(seeded_with_prose_context: Any) -> None:
    cfg, tag = seeded_with_prose_context
    [row] = _run_rows(cfg, tag)
    assert row.quote == EXACT_1
    # prefix/suffix are the prose anchors h stores around the selection, surfaced for the CLI.
    assert row.quote_prefix == "For classifiers "
    assert row.quote_suffix == " is the pullback"
    assert row.page_index is None  # a non-paginated (HTML) document has no PageSelector
