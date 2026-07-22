"""h HTTP API client for writes (``acted`` tagging).

Reads go through Postgres (``source.py``); only writes use the h HTTP API with a
developer token. The one write is merging the ``acted`` tag onto a resolved annotation.
"""

from __future__ import annotations

from types import TracebackType

import httpx


class ResponseContractError(RuntimeError):
    """The h API returned a response that violates its own contract.

    Raised instead of coercing missing or malformed required fields into defaults: a
    PATCH built from coerced state would silently destroy server data
    (hypothesis-review#7).
    """


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
        # h PATCH replaces the tags field, so merge against the current tags. The current
        # tags must actually be present and well-formed: coercing a malformed response to
        # [] would make the PATCH wipe the annotation's existing tags
        # (hypothesis-review#7).
        current = self._client.get(f"/api/annotations/{annotation_id}")
        current.raise_for_status()
        existing = current.json().get("tags")
        if not isinstance(existing, list) or not all(isinstance(t, str) for t in existing):
            msg = f"annotation {annotation_id}: response `tags` is {existing!r}, not a list of strings; refusing to PATCH a merge built from a malformed response"
            raise ResponseContractError(msg)
        merged = list(dict.fromkeys([*existing, *add]))
        resp = self._client.patch(f"/api/annotations/{annotation_id}", json={"tags": merged})
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
