"""Ledger: append web feedback to a git-tracked JSONL file in the ambient repo.

``append`` rejects a second drain of the same annotation, so each ledger row carries one
unambiguous remediation. No deploy-log or version anchoring: the ledger is
committed, so git's own history places each entry next to the code state it landed
against. ``track`` is exercised against a throwaway repo with an always-failing hook,
proving it commits the ledger alone (feedback is data, not code, and must record while the
repo is mid-edit) and leaves unrelated in-progress work untouched.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

from annotate.ledger import append, repo_root, resolve, track
from annotate.models import Annotation, LedgerEntry


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


def test_append_writes_one_jsonl_line_per_new_annotation(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "feedback" / "ledger.jsonl"  # parent created on demand
    new = append(
        [_ann("a1", "fix typo"), _ann("a2", "add ref")],
        ledger,
        {"a1": "Corrected the typo.", "a2": "Added the missing reference."},
    )

    assert [e.id for e in new] == ["a1", "a2"]
    lines = ledger.read_text().splitlines()
    assert [LedgerEntry.from_json(line).text for line in lines] == ["fix typo", "add ref"]
    assert [LedgerEntry.from_json(line).remediation for line in lines] == [
        "Corrected the typo.",
        "Added the missing reference.",
    ]


def test_append_rejects_a_second_drain_of_the_same_annotation(tmp_path: pathlib.Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    append([_ann("a1")], ledger, {"a1": "First remediation."})

    with pytest.raises(ValueError):
        append([_ann("a1")], ledger, {"a1": "Second remediation."})
    [entry] = [LedgerEntry.from_json(line) for line in ledger.read_text().splitlines()]
    assert entry.remediation == "First remediation."


def test_resolve_keeps_path_inside_repo_and_rejects_escape(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    assert resolve(pathlib.Path("feedback/ledger.jsonl"), root) == (root / "feedback/ledger.jsonl").resolve()
    with pytest.raises(ValueError):
        resolve(pathlib.Path("../outside.jsonl"), root)


def test_repo_root_is_none_outside_a_git_repo(tmp_path: pathlib.Path) -> None:
    assert repo_root(tmp_path) is None  # a bare tmp dir is not a git repo


# --- track() / repo_root() against a throwaway repo with a failing hook ---------


def _git(repo: pathlib.Path, *args: str) -> None:
    # core.hooksPath=/dev/null bypasses BOTH the machine-wide gate and the repo's own
    # failing hook, for test setup only. The code under test uses --no-verify.
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
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
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_track_commits_only_the_ledger_and_bypasses_the_hook(tmp_path: pathlib.Path) -> None:
    repo = _init_repo(tmp_path)
    ledger = repo / "feedback" / "ledger.jsonl"
    append([_ann("a1")], ledger, {"a1": "Applied the requested correction."})
    (repo / "wip.txt").write_text("unrelated in-progress work")  # must stay uncommitted

    track(ledger, "feedback: 1 annotation")  # fails here unless it uses --no-verify

    files = _committed_files(repo)
    assert "feedback/ledger.jsonl" in files  # ledger landed
    assert "wip.txt" not in files  # only the ledger, not the mid-edit work
    dirty = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert "wip.txt" in dirty  # in-progress work untouched


def test_repo_root_returns_toplevel_from_a_subdir(tmp_path: pathlib.Path) -> None:
    repo = _init_repo(tmp_path)
    sub = repo / "a" / "b"
    sub.mkdir(parents=True)
    root = repo_root(sub)
    assert root is not None
    assert root.resolve() == repo.resolve()
