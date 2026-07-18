"""Version-anchoring: bind an annotation to the commit that was live when it was
written, and derive git rewind/delta commands from a ledger entry.

The reviewed repo's deploy writes a **deploy-log**: one ``<iso8601>\\t<sha>`` line
per deploy. :func:`commit_at` finds the commit whose deploy interval contains a
timestamp — the latest deploy at or before it (DESIGN.md §6). ``rewind``/``delta``
shell ``git`` in the reviewed repo (the process CWD).
"""

from __future__ import annotations

import bisect
import pathlib
import subprocess
from datetime import datetime
from typing import Any

from annotate.models import LedgerEntry


def _to_dt(ts: Any) -> datetime:
    return ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))


def _parse_deploy_log(deploy_log: pathlib.Path) -> list[tuple[datetime, str]]:
    entries: list[tuple[datetime, str]] = []
    for raw in deploy_log.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        iso, sha = line.split("\t")
        entries.append((_to_dt(iso), sha))
    entries.sort(key=lambda e: e[0])
    return entries


def commit_at(ts: Any, deploy_log: pathlib.Path) -> str:
    """Return the sha whose deploy interval contains ``ts`` (the latest deploy at
    or before it). Raises ``ValueError`` if ``ts`` predates every deploy."""
    entries = _parse_deploy_log(deploy_log)
    idx = bisect.bisect_right([dt for dt, _ in entries], _to_dt(ts))
    if idx == 0:
        raise ValueError(f"{ts} predates every deploy in {deploy_log}")
    return entries[idx - 1][1]


def rewind(entry: LedgerEntry) -> str:
    """Verify the entry's commit exists (``git rev-parse``) and return the
    ``git checkout`` command that restores the reviewed doc to its state when the
    feedback was written. Non-destructive: returns the command, does not run it."""
    # "^{commit}" peels the object to a commit: fails if the sha is absent from
    # the repo or isn't a commit. Bare "--verify <sha>" only checks syntax.
    subprocess.run(
        ["git", "rev-parse", "--verify", entry.commit + "^{commit}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return f"git checkout {entry.commit}"


def delta(entry: LedgerEntry) -> str:
    """Return ``git diff <commit>..HEAD -- <page-source>`` — how the annotated
    page source has changed since the feedback was written."""
    result = subprocess.run(
        ["git", "diff", f"{entry.commit}..HEAD", "--", entry.uri],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout
