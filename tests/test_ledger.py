from annotate.ledger import append
from annotate.models import Annotation, Batch, LedgerEntry


def _deploy_log(tmp_path):
    p = tmp_path / "deploy-log.tsv"
    p.write_text("2026-07-18T09:00:00\tsha_early\n2026-07-18T11:00:00\tsha_late\n")
    return p


def _ann(id, created, tags=None):
    return Annotation(
        id=id,
        created=created,
        userid="acct:me",
        group="grp",
        uri=f"http://localhost/{id}.html",
        text=f"note {id}",
        tags=tags or [],
        target=[{"selector": [{"type": "TextQuoteSelector", "exact": id}]}],
    )


def _batch(anns):
    open_m = _ann("open", "2026-07-18T09:30:00", ["review:open"])
    send_m = _ann("send", "2026-07-18T12:00:00", ["review:send"])
    return Batch(open_marker=open_m, send_marker=send_m, annotations=anns)


def test_append_writes_one_commit_stamped_line_per_annotation(tmp_path):
    deploy_log = _deploy_log(tmp_path)
    ledger = tmp_path / "annotate-ledger.jsonl"
    anns = [
        _ann("a", "2026-07-18T10:00:00"),  # between deploys -> sha_early
        _ann("b", "2026-07-18T11:30:00"),  # after deploy2 -> sha_late
    ]

    entries = append(_batch(anns), ledger, deploy_log)

    assert [e.commit for e in entries] == ["sha_early", "sha_late"]
    assert all(e.state == "open" for e in entries)

    lines = ledger.read_text().splitlines()
    assert len(lines) == 2
    round_tripped = [LedgerEntry.from_json(ln) for ln in lines]
    assert round_tripped == entries


def test_append_is_append_only(tmp_path):
    deploy_log = _deploy_log(tmp_path)
    ledger = tmp_path / "annotate-ledger.jsonl"
    batch = _batch([_ann("a", "2026-07-18T10:00:00")])
    append(batch, ledger, deploy_log)
    append(batch, ledger, deploy_log)
    assert len(ledger.read_text().splitlines()) == 2
