"""Model tests.

``from_pg_row`` is deliberately NOT unit-tested against a hand-built row: a synthetic
row encodes an assumption about the h schema, and if that assumption is wrong the test
passes while the real query fails (which is precisely the bug that shipped). The
column mapping is proved against real rows in ``test_source.py``. Time-window batching is
proved through ``batch_since`` in ``test_session.py``. What remains here is the ledger's
own serialization contract.
"""

from annotate.models import LedgerEntry


def test_ledger_entry_json_round_trip():
    entry = LedgerEntry(
        id="2362ce89-82a6-11f1-9030-6f51a56e1c15",
        created="2026-07-18T10:00:00",
        uri="http://localhost/paper.html",
        text="needs a citation",
        tags=["review:open"],
        target=[{"source": "http://localhost/paper.html", "selector": []}],
        quote="Let $f(x,y)$ be a polynomial",
    )
    assert LedgerEntry.from_json(entry.to_json()) == entry
