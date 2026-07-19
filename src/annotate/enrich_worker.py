"""Eager enrichment worker: normalize new annotations into ``annotation_normalized``.

At intake — not at display — an annotation's flattened quote is turned into a display-ready
quote with rendered math recovered, and stored keyed to the annotation. Every view then reads
that stored field (the h API joins it); the raw capture is used only for anchoring, and the
annotation row is never touched.

This runs beside h rather than inside it: it polls the search API for annotations that have no
normalized row yet and enriches them. PDF annotations (a ``PageSelector``) are OCR'd here in
pure Python. HTML annotations need the page's KaTeX rendering to match the flattened quote — a
JS concern — so they are left for the client's reconstruction (the API's raw-quote fallback
covers them until then); the worker records ``method='raw'`` only when a document genuinely
carries no recoverable math.

Run: ``python -m annotate.enrich_worker`` (continuous poll) or ``--once`` (single backfill pass).
"""

from __future__ import annotations

import argparse
import base64
import time
import uuid

import httpx
import psycopg

from annotate.config import Config
from annotate.pdfmath import clean_pdf_quote
from annotate.ocr_server import resolve_pdf_url


def _db_uuid(public_id: str) -> str:
    """h's URL-safe-base64 annotation id -> the UUID stored in Postgres."""
    return str(uuid.UUID(bytes=base64.urlsafe_b64decode(public_id + "==")))


def _selectors(annotation: dict) -> list[dict]:
    targets = annotation.get("target") or []
    return targets[0].get("selector", []) if targets else []


def _quote(selectors: list[dict]) -> str:
    for selector in selectors:
        if selector.get("type") == "TextQuoteSelector":
            return selector.get("exact", "")
    return ""


def normalize(annotation: dict) -> tuple[str, str] | None:
    """``(normalized_quote, method)`` for an annotation, or ``None`` to leave it for another
    path. PDF annotations are OCR'd; HTML annotations are not handled here (see module docstring)."""
    selectors = _selectors(annotation)
    quote = _quote(selectors)
    if not quote:
        return None
    page = next(
        (s.get("index") for s in selectors if s.get("type") == "PageSelector"), None
    )
    if not isinstance(page, int):
        return None  # not a PDF annotation -> client reconstructs from the HTML source
    url = resolve_pdf_url(annotation.get("uri", ""))
    if url is None:
        return None
    clean = clean_pdf_quote(url, page, "", "", quote)
    return (clean, "ocr")


def _already_normalized(conn: psycopg.Connection, db_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM annotation_normalized WHERE annotation_id = %s", (db_id,)
        )
        return cur.fetchone() is not None


def _store(conn: psycopg.Connection, db_id: str, normalized_quote: str, method: str) -> None:
    conn.execute(
        "INSERT INTO annotation_normalized (annotation_id, normalized_quote, method) "
        "VALUES (%s, %s, %s) ON CONFLICT (annotation_id) DO UPDATE SET "
        "normalized_quote = EXCLUDED.normalized_quote, method = EXCLUDED.method, updated = now()",
        (db_id, normalized_quote, method),
    )


def enrich_once(cfg: Config, limit: int = 200) -> int:
    """Enrich every not-yet-normalized annotation the search returns. Count enriched."""
    base = cfg.api_url.rstrip("/")
    headers = {"Authorization": f"Bearer {cfg.token}"}
    rows = (
        httpx.get(
            f"{base}/api/search",
            params={"limit": limit, "sort": "created", "order": "desc"},
            headers=headers,
            timeout=30,
        )
        .json()
        .get("rows", [])
    )
    enriched = 0
    with psycopg.connect(cfg.pg_dsn) as conn:
        for annotation in rows:
            db_id = _db_uuid(annotation["id"])
            if _already_normalized(conn, db_id):
                continue
            result = normalize(annotation)
            if result is None:
                continue
            _store(conn, db_id, *result)
            enriched += 1
        conn.commit()
    return enriched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="one backfill pass, then exit")
    parser.add_argument("--interval", type=float, default=5.0, help="poll seconds")
    args = parser.parse_args()
    cfg = Config.load()
    if args.once:
        print(f"enriched {enrich_once(cfg)} annotation(s)")
        return
    while True:
        try:
            count = enrich_once(cfg)
            if count:
                print(f"enriched {count} annotation(s)")
        except Exception as exc:  # noqa: BLE001 - a poll worker must not die on one bad round
            print(f"enrich round failed: {exc!r}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
