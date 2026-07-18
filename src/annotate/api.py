"""h HTTP API client for writes (session markers + ``acted`` tagging).

Reads go through Postgres (``source.py``); only writes use the h HTTP API with a
developer token. Markers are annotations anchored on a synthetic URI so they
never collide with real page annotations.
"""

from __future__ import annotations

from types import TracebackType

import httpx

MARKER_URI = "urn:annotate:marker"


class HClient:
    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=api_url,
            headers={"Authorization": f"Bearer {token}"},
            transport=transport,
        )

    def create_marker(self, group_id: str, kind: str) -> str:
        resp = self._client.post(
            "/api/annotations",
            json={"uri": MARKER_URI, "group": group_id, "text": kind, "tags": [kind]},
        )
        resp.raise_for_status()
        return resp.json()["id"]

    def tag(self, annotation_id: str, add: list[str]) -> None:
        # h PATCH replaces the tags field, so merge against the current tags.
        current = self._client.get(f"/api/annotations/{annotation_id}")
        current.raise_for_status()
        existing = current.json().get("tags") or []
        merged = list(dict.fromkeys([*existing, *add]))
        resp = self._client.patch(
            f"/api/annotations/{annotation_id}", json={"tags": merged}
        )
        resp.raise_for_status()

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()
