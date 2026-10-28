"""HTTP client for the Eurostat SDMX 2.1 dissemination API.

Responsibilities: build the request URL, send it with a descriptive User-Agent, retry
transient failures, and turn Eurostat's JSON error bodies into readable exceptions.
It does NOT parse the CSV; that is ``bronze``/``pipeline`` territory.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ingestion.schemas import DatasetConfig

logger = logging.getLogger("ingestion.client")

USER_AGENT = "europulse/0.1 (+https://github.com/Vazc0-0/europulse)"


class EurostatError(RuntimeError):
    """Base class for download problems."""


class EurostatAPIError(EurostatError):
    """A non-retryable 4xx answer (bad key, unknown dataset...)."""


class EurostatTransientError(EurostatError):
    """A 429/5xx answer. Retried; only surfaces once attempts are exhausted."""


def build_url(base_url: str, dataset: DatasetConfig, geos: list[str]) -> str:
    """``{base_url}/{code}/{key}?format=SDMX-CSV&startPeriod=...``.

    Geos are joined with ``+`` (SDMX "OR") inside the key. We never add geo filters as query
    parameters: the endpoint ignores them and would return the whole dataset.
    """
    key = dataset.key.replace("{geo}", "+".join(geos))
    return (
        f"{base_url.rstrip('/')}/{dataset.code}/{key}"
        f"?format=SDMX-CSV&startPeriod={dataset.start_period}"
    )


@dataclass(frozen=True)
class FetchResult:
    url: str
    text: str
    # Moment the response was received; becomes ``_ingested_at`` in bronze.
    fetched_at: datetime


def _error_label(response: httpx.Response) -> str:
    """Extract Eurostat's own explanation from ``{"error":[{"label":...}]}`` if present."""
    try:
        errors = response.json().get("error", [])
        labels = [str(e.get("label", e)) for e in errors]
        if labels:
            return "; ".join(labels)
    except (ValueError, AttributeError):
        pass
    return response.text[:200].strip() or "no error body"


def _is_retryable(exc: BaseException) -> bool:
    # Timeouts and connection problems are all httpx.TransportError subclasses.
    return isinstance(exc, httpx.TransportError | EurostatTransientError)


class EurostatClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float,
        max_attempts: int,
        backoff_initial: float = 1.0,
        backoff_max: float = 30.0,
    ) -> None:
        self.base_url = base_url
        self.max_attempts = max_attempts
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._http = httpx.Client(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )

    def __enter__(self) -> EurostatClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._http.close()

    def fetch_dataset(self, dataset: DatasetConfig, geos: list[str]) -> FetchResult:
        url = build_url(self.base_url, dataset, geos)
        retrying = Retrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(self.max_attempts),
            # Jitter avoids hammering the API in lockstep if several runs retry together.
            wait=wait_exponential_jitter(
                multiplier=self._backoff_initial,
                jitter=self._backoff_initial,
                max=self._backoff_max,
            ),
            before_sleep=lambda state: self._log_retry(state, dataset.code, url),
            reraise=True,
        )
        return retrying(self._get_once, dataset.code, url)

    @staticmethod
    def _log_retry(state: RetryCallState, code: str, url: str) -> None:
        exc = state.outcome.exception() if state.outcome else None
        logger.warning(
            "retrying after transient failure",
            extra={"dataset": code, "url": url, "attempt": state.attempt_number, "error": str(exc)},
        )

    def _get_once(self, code: str, url: str) -> FetchResult:
        response = self._http.get(url)
        status = response.status_code
        if status == 429 or status >= 500:
            raise EurostatTransientError(f"{code}: HTTP {status} from Eurostat ({url})")
        if status >= 400:
            # Eurostat answers with JSON even though we asked for CSV; surface its label.
            raise EurostatAPIError(
                f"{code}: HTTP {status} from Eurostat: {_error_label(response)} ({url})"
            )
        # utf-8-sig drops a BOM if one is present, which would corrupt the first header name.
        text = response.content.decode("utf-8-sig")
        logger.info("downloaded", extra={"dataset": code, "url": url, "bytes": len(text)})
        return FetchResult(url=url, text=text, fetched_at=datetime.now(UTC))
