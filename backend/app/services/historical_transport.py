"""Shared read-only HTTP response and injectable history transport contract."""

from dataclasses import dataclass
from typing import Mapping, Protocol


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class HistoricalTransport(Protocol):
    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> HttpResponse: ...
