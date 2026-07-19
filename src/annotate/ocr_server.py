"""Display-time OCR endpoint for PDF math quotes.

A PDF annotation's quote is the flattened text layer; the clean LaTeX only exists after OCR,
which needs the Mathpix key (server-side). The sidebar client can't recover it from the page
(no x-tex layer) and must not mutate the stored annotation, so instead it POSTs the
annotation's region here at render time and gets back ``\\(…\\)`` LaTeX. Nothing is stored;
the annotation is never touched.

``POST /ocr`` with ``{uri, page_index, prefix, suffix, exact}`` -> ``{"latex": "\\(…\\)"}``
(or ``{"latex": null}`` when the document can't be resolved or OCR recovers nothing). CORS is
open so the browser client can call it cross-origin.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import psycopg

from annotate.config import Config
from annotate.pdfmath import clean_pdf_quote


def resolve_pdf_url(uri: str) -> str | None:
    """A fetchable http(s) URL for a PDF annotation's document, or ``None``. An http URI is
    itself fetchable; a ``urn:x-pdf:`` fingerprint (what a locally-opened PDF anchors to) is
    resolved to a sibling http URL via h's ``document_uri`` table -- a PDF downloaded from the
    web keeps its source URL alongside the fingerprint, so the bytes are fetchable."""
    if uri.startswith(("http://", "https://")):
        return uri
    if not uri.startswith("urn:x-pdf:"):
        return None
    with psycopg.connect(Config.load().pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT du2.uri FROM document_uri du1 JOIN document_uri du2 "
            "ON du1.document_id = du2.document_id "
            "WHERE du1.uri = %s AND du2.uri LIKE 'http%%' LIMIT 1",
            (uri,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def ocr_latex(payload: dict) -> str | None:
    """Clean ``\\(…\\)`` LaTeX for a PDF math region, or ``None`` when nothing is recovered.
    Pure delegation to :func:`resolve_pdf_url` + :func:`clean_pdf_quote`, so the server itself
    holds no logic worth testing separately."""
    uri = payload.get("uri", "")
    page = payload.get("page_index")
    exact = payload.get("exact", "")
    url = resolve_pdf_url(uri) if uri else None
    if url is None or not isinstance(page, int):
        return None
    clean = clean_pdf_quote(url, page, payload.get("prefix", ""), payload.get("suffix", ""), exact)
    return clean if clean != exact else None


class _Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802  (CORS preflight)
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        body = json.dumps({"latex": ocr_latex(payload)}).encode()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002  keep the endpoint quiet
        pass


def serve(port: int = 8901) -> None:
    ThreadingHTTPServer(("127.0.0.1", port), _Handler).serve_forever()


if __name__ == "__main__":
    serve()
