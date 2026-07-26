"""h HTTP API client for deleting drained annotations.

Reads go through Postgres (``source.py``); writes use the h HTTP API with a
developer token.
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

    def delete(self, annotation_id: str) -> None:
        """Delete an annotation after its remediation is committed to the ledger."""
        resp = self._client.delete(f"/api/annotations/{annotation_id}")
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
