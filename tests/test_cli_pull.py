import json
from datetime import datetime

from click.testing import CliRunner

from annotate.api import HClient
from annotate.cli import App, main
from annotate.models import Annotation


def _ann(id: str, created: int, tags: list[str] | None = None) -> Annotation:
    return Annotation(
        id=id,
        created=created,
        userid="acct:me@localhost",
        group="grp",
        uri=f"http://localhost/{id}",
        text=id,
        tags=tags or [],
        target=None,
    )


class _StubSource:
    """In-memory :class:`~annotate.source.AnnotationSource` for the CLI tests."""

    def __init__(self, anns: list[Annotation]) -> None:
        self._anns = anns

    def list(
        self,
        group_id: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Annotation]:
        rows = [a for a in self._anns if a.group == group_id]
        return sorted(rows, key=lambda a: a.created)


class _StubClient(HClient):
    """Records marker posts without opening an httpx client or touching the API."""

    def __init__(self) -> None:
        self.markers: list[tuple[str, str]] = []

    def create_marker(self, group_id: str, kind: str) -> str:
        self.markers.append((group_id, kind))
        return "marker-1"


OPEN = _ann("open", 1, ["review:open"])
A = _ann("a", 2)  # paperA
B = _ann("b", 3)  # paperB
SEND = _ann("send", 4, ["review:send"])


def _app(anns: list[Annotation], client: HClient | None = None) -> App:
    return App(source=_StubSource(anns), client=client or _StubClient(), group_id="grp")


def test_pull_prints_batch_between_open_and_send() -> None:
    result = CliRunner().invoke(main, ["pull"], obj=_app([OPEN, A, B, SEND]))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.output)] == ["a", "b"]


def test_pull_prints_empty_list_when_no_open_session() -> None:
    result = CliRunner().invoke(main, ["pull"], obj=_app([A, B]))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_pull_empty_when_session_opened_but_not_sent() -> None:
    result = CliRunner().invoke(main, ["pull"], obj=_app([OPEN, A, B]))
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_wait_opens_marker_then_prints_batch_on_first_tick() -> None:
    client = _StubClient()
    result = CliRunner().invoke(main, ["wait"], obj=_app([OPEN, A, B, SEND], client=client))
    assert result.exit_code == 0, result.output
    assert [a["id"] for a in json.loads(result.output)] == ["a", "b"]
    assert client.markers == [("grp", "review:open")]  # posted exactly one open marker
