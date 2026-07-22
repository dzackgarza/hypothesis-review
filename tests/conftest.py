"""Shared fixtures for CLI tests whose commands record into a git repo.

The recording commands (`pull`/`wait`/`record`) commit the ledger with ``--no-verify``,
so they work against a plain throwaway repo; ``git_repo`` also chdirs into it, since the
commands resolve the repo from the current working directory.
"""

import os
import subprocess
from pathlib import Path

import pytest

#: Live-boundary opt-in flags, as declared next to the markers in pyproject.toml. The
#: pg/e2e tests drive the real self-hosted h stack (Postgres + API); a runner without
#: that stack cannot execute them, so they are collected only when their flag is set.
#: This is explicit collection-time deselection with a visible count -- never a skip
#: that reports the burden as exercised. The burden is discharged on the machine that
#: runs the stack via the integrated proof workflow.
_OPT_IN_FLAGS = {"pg": "ANNOTATE_PG_IT", "e2e": "ANNOTATE_E2E"}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    disabled = {marker: flag for marker, flag in _OPT_IN_FLAGS.items() if os.environ.get(flag) != "1"}
    if not disabled:
        return
    kept: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        gated = [m for m in disabled if item.get_closest_marker(m)]
        (deselected if gated else kept).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


def _git(repo: Path, *args: str) -> None:
    # core.hooksPath=/dev/null keeps this throwaway repo off the machine-wide commit gate.
    subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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


def committed_at_head(repo: Path) -> str:
    """The file paths in the repo's most recent commit."""
    return subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--format=", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
