import subprocess

import pytest

from annotate.anchor import commit_at, delta, rewind
from annotate.models import LedgerEntry


def _deploy_log(tmp_path):
    p = tmp_path / "deploy-log.tsv"
    p.write_text("2026-07-18T09:00:00\tdeadbeef1\n2026-07-18T11:00:00\tcafef00d2\n")
    return p


def test_commit_at_returns_earlier_sha_between_deploys(tmp_path):
    log = _deploy_log(tmp_path)
    # 10:00 is after deploy1 (09:00), before deploy2 (11:00) -> deploy1 is live.
    assert commit_at("2026-07-18T10:00:00", log) == "deadbeef1"


def test_commit_at_boundary_and_after_last(tmp_path):
    log = _deploy_log(tmp_path)
    assert commit_at("2026-07-18T11:00:00", log) == "cafef00d2"  # exactly at deploy2
    assert commit_at("2026-07-18T23:00:00", log) == "cafef00d2"  # after last deploy


def test_commit_at_before_first_deploy_raises(tmp_path):
    log = _deploy_log(tmp_path)
    with pytest.raises(ValueError):
        commit_at("2026-07-18T08:00:00", log)


# --- rewind / delta against a real throwaway git repo ---


def _git(cwd, *args):
    # -c core.hooksPath=/dev/null isolates this throwaway repo from the
    # machine-wide ai-review-ci commit gate (global core.hooksPath).
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _seed_repo(tmp_path):
    repo = tmp_path / "reviewed"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "paper.html").write_text("v1\n")
    _git(repo, "add", "paper.html")
    _git(repo, "commit", "-q", "-m", "v1")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, sha


def _entry(commit, uri="paper.html"):
    return LedgerEntry(
        id="a1",
        created="2026-07-18T10:00:00",
        uri=uri,
        text="fix",
        tags=[],
        target=None,
        commit=commit,
        state="open",
    )


def test_rewind_returns_checkout_cmd_for_live_commit(tmp_path, monkeypatch):
    repo, sha = _seed_repo(tmp_path)
    monkeypatch.chdir(repo)
    assert rewind(_entry(sha)) == f"git checkout {sha}"


def test_rewind_raises_on_unknown_commit(tmp_path, monkeypatch):
    repo, _ = _seed_repo(tmp_path)
    monkeypatch.chdir(repo)
    with pytest.raises(subprocess.CalledProcessError):
        rewind(_entry("0" * 40))


def test_delta_diffs_page_source_since_commit(tmp_path, monkeypatch):
    repo, sha = _seed_repo(tmp_path)
    (repo / "paper.html").write_text("v2 changed\n")
    _git(repo, "commit", "-q", "-am", "v2")
    monkeypatch.chdir(repo)
    out = delta(_entry(sha))
    assert "v1" in out and "v2 changed" in out  # the diff shows the change
