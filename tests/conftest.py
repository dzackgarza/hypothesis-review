"""Shared fixtures for CLI tests whose commands record into a git repo.

The recording commands (`pull`/`wait`/`record`) commit the ledger with ``--no-verify``,
so they work against a plain throwaway repo; ``git_repo`` also chdirs into it, since the
commands resolve the repo from the current working directory.
"""

import subprocess

import pytest


def _git(repo, *args):
    # core.hooksPath=/dev/null keeps this throwaway repo off the machine-wide commit gate.
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    )


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A throwaway git repo with the working directory inside it."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "README").write_text("proj\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "init")
    monkeypatch.chdir(repo)
    return repo


def committed_at_head(repo):
    """The file paths in the repo's most recent commit."""
    return subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout
