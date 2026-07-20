"""h HTTP API client for writes (``acted`` tagging).

Reads go through Postgres (``source.py``); only writes use the h HTTP API with a
developer token. The one write is merging the ``acted`` tag onto a resolved annotation.
"""

from __future__ import annotations

from types import TracebackType

import httpx


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
