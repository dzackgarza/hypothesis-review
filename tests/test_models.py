from annotate.models import Annotation, LedgerEntry


def _row():
    return {
        "id": "anno-1",
        "created": "2026-07-18T10:00:00",
        "userid": "acct:dzack@localhost",
        "groupid": "grp-xyz",
        "uri": "http://localhost/paper.html",
        "text": "needs a citation",
        "tags": ["review:open"],
        "target": [{"selector": [{"type": "TextQuoteSelector", "exact": "foo"}]}],
    }


def test_from_pg_row_maps_columns():
    row = _row()
    ann = Annotation.from_pg_row(row)
    assert ann.id == "anno-1"
    assert ann.created == "2026-07-18T10:00:00"
    assert ann.userid == "acct:dzack@localhost"
    assert ann.group == "grp-xyz"  # groupid -> group
    assert ann.uri == "http://localhost/paper.html"
    assert ann.text == "needs a citation"
    assert ann.tags == ["review:open"]
    assert ann.target == row["target"]  # selector JSON passed through verbatim


def test_is_marker_true_iff_tag_present():
    assert Annotation.from_pg_row(_row()).is_marker("review:open") is True
    plain = _row() | {"tags": ["paperA"]}
    assert Annotation.from_pg_row(plain).is_marker("review:open") is False


def test_ledger_entry_json_round_trip():
    entry = LedgerEntry(
        id="anno-1",
        created="2026-07-18T10:00:00",
        uri="http://localhost/paper.html",
        text="needs a citation",
        tags=["review:open"],
        target=[{"selector": [{"type": "TextQuoteSelector", "exact": "foo"}]}],
        commit="abc1234",
        state="open",
    )
    assert LedgerEntry.from_json(entry.to_json()) == entry
