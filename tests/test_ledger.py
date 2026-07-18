"""Ledger: append web feedback to a git-tracked JSONL file in the ambient repo.

No deploy-log, no version anchoring -- the ledger is committed, so git's own history
places each entry next to the code state it landed against. ``track`` is exercised
against a throwaway repo carrying an always-failing hook, proving it commits anyway
(feedback is data, not code, and must record while the repo is mid-edit) and that it
commits the ledger only, leaving unrelated in-progress work untouched.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from annotate.ledger import append, repo_root, resolve, track
from annotate.models import Annotation, Batch, LedgerEntry


def _ann(id: str, text: str = "note", target: object = None) -> Annotation:
    return Annotation(
        id=id,
        created="2026-07-18T10:00:00",
        userid="acct:me@localhost",
        group="grp",
        uri=f"http://localhost/{id}.html",
        text=text,
        tags=[],
        target=target,
    )


def _batch(anns: list[Annotation]) -> Batch:
    return Batch(open_marker=_ann("open"), send_marker=_ann("send"), annotations=anns)


def test_append_writes_one_jsonl_line_per_annotation(tmp_path):
    ledger = tmp_path / ".annotations" / "feedback.jsonl"  # parent created on demand
    entries = append(_batch([_ann("a1", "fix typo"), _ann("a2", "add ref")]), ledger)

    assert [e.id for e in entries] == ["a1", "a2"]
    lines = ledger.read_text().splitlines()
    assert [LedgerEntry.from_json(line).text for line in lines] == ["fix typo", "add ref"]


def test_append_accumulates_across_calls(tmp_path):
    ledger = tmp_path / "feedback.jsonl"
    append(_batch([_ann("a1")]), ledger)
    append(_batch([_ann("a2")]), ledger)
    ids = [LedgerEntry.from_json(line).id for line in ledger.read_text().splitlines()]
    assert ids == ["a1", "a2"]  # append-only, prior entries preserved


def test_resolve_keeps_path_inside_repo_and_rejects_escape(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    assert resolve(pathlib.Path("notes/f.jsonl"), root) == (root / "notes/f.jsonl").resolve()
    with pytest.raises(ValueError):
        resolve(pathlib.Path("../outside.jsonl"), root)


# --- track() / repo_root() against a throwaway repo with a failing hook ---------


def _git(repo: pathlib.Path, *args: str) -> None:
    # core.hooksPath=/dev/null bypasses BOTH the machine-wide gate and the repo's
    # own failing hook, for test setup only. The code under test uses --no-verify.
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    )


def _init_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    hooks = repo / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")  # a gate that blocks any ordinary commit
    hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    (repo / "README").write_text("proj\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _committed_files(repo: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout


def test_track_commits_only_the_ledger_and_bypasses_the_hook(tmp_path):
    repo = _init_repo(tmp_path)
    ledger = repo / ".annotations" / "feedback.jsonl"
    append(_batch([_ann("a1")]), ledger)
    (repo / "wip.txt").write_text("unrelated in-progress work")  # must stay uncommitted

    track(ledger, "feedback: 1 annotation")  # fails here unless it uses --no-verify

    files = _committed_files(repo)
    assert ".annotations/feedback.jsonl" in files  # ledger landed
    assert "wip.txt" not in files  # only the ledger, not the mid-edit work
    dirty = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout
    assert "wip.txt" in dirty  # in-progress work untouched


def test_repo_root_returns_toplevel_from_a_subdir(tmp_path):
    repo = _init_repo(tmp_path)
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    assert repo_root(sub).resolve() == repo.resolve()
