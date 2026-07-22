# Integrated cross-repo proof — evidence index (issues #6 / #7)

Run 2026-07-22 on the machine hosting the live stack: h fork (gunicorn, dev config,
Postgres 16 + Elasticsearch 7.10.2+ICU, kombu `memory://` broker — realtime fan-out is
not among the claims proven here), client harness (`dev-server`, asset server on a free
port), unpacked extension build (stable manifest key → id
`mfciknjpndbbjbmlebhbonekfflcibbk`, OAuth authclient registered against it), Chromium
via Playwright under Xvfb.

## Live API legs (all through `POST /api/annotations` with a real token)

| leg | input | observed |
|---|---|---|
| HTML math recovery | quote `let x2 be a square and this is prose` | stored + returned `normalized_quote: let $x^{2}$ be a square and this is prose` (id `zri_OIWy…`) |
| HTML prose identity | `this is prose for the proof` | returned byte-identical |
| Page note | no target | 200, no normalization row, no error |
| Reply | `references: [parent]`, no quote | 200, saved |
| Failed recovery | quote absent from the page | structured error (`description`, `reason`, `retryable: true`, `diagnostic_id`), HTTP 500, **0 rows persisted** (rollback reread via Postgres) |

## Review-session lifecycle (real processes, real loopback)

- `annotate wait` parked the open time and served `127.0.0.1:8902`.
- An annotation created *during* the window was the exact delivered batch — with the
  normalized quote — after `POST /session/close` (204; the close server's request log
  emitted on stderr).
- Delivery recorded to the throwaway repo's git-tracked `feedback/ledger.jsonl` and
  committed before printing.
- `annotate resolve` merged `acted` via the live API: Postgres reread shows
  `{__proof_session2, acted}` — merged, not wiped.
- Full test suite with the live-boundary opt-in: `ANNOTATE_PG_IT=1` → **50 passed**.

## Browser legs (real unpacked extension, Xvfb Chromium)

- `02-authorize-form.png` — the OAuth popup at `/oauth/authorize` (web_message flow,
  registered client id, extension origin), filled login form.
- Login succeeded: the trusted client auto-granted, the popup self-closed, and the
  session **persisted across separate browser launches** (subsequent runs skip login).
- `04-logged-in-cards.png` — the extension sidebar on the annotated page: the card
  renders the server-normalized quote with **inline MathJax x² in the text flow**
  (client#1 fix live), the reply nested beneath, anchoring highlight in the page, and
  the injected Send-to-agent control.
- `05-send-clicked.png` — after the real button click: the click woke the actually
  waiting `annotate wait` process (204 loopback close), which delivered exactly the
  in-window annotation (`__proof_click`, and again `__proof_fullflow` on a fresh
  profile) and recorded it to the ledger.

Not proven here: Mathpix OCR legs (PDF and source-less-HTML) — no `MATHPIX_API_KEY` on
this machine; the failed-recovery leg above exercises that path's fail-loud contract
instead. Realtime websocket fan-out (memory broker) is likewise out of scope of these
claims.
