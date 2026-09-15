import logging
import random
import re
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 8
BACKOFF_SECONDS = 3
MAX_BACKOFF_SECONDS = 180

UNITS_PER_MINUTE = {
    "archive-api.open-meteo.com": 600,
    "marine-api.open-meteo.com": 600,
    "gateway.api.globalfishingwatch.org": 30,
}

_sent_at: dict[str, float] = {}


def _throttle(url: str, cost: int) -> None:
    host = urlsplit(url).hostname or ""
    limit = UNITS_PER_MINUTE.get(host)
    if limit is None:
        return
    interval = 60.0 * cost / limit
    wait = interval - (time.monotonic() - _sent_at.get(host, 0.0))
    if wait > 0:
        logger.debug("%s: spacing %d unit(s), waiting %.1fs", host, cost, wait)
        time.sleep(wait)
    _sent_at[host] = time.monotonic()


class QuotaExhausted(RuntimeError):
    def __init__(self, host: str, reason: str, retry_at: datetime) -> None:
        super().__init__(f"{host}: {reason}")
        self.retry_at = retry_at


_SPENT_QUOTA = re.compile(r"\b(daily|monthly)\b", re.IGNORECASE)

_blocked: dict[str, tuple[datetime, str]] = {}


def _midnight(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC)


def _spent_until(reason: str, now: datetime) -> datetime | None:
    spent = _SPENT_QUOTA.search(reason)
    if spent is None:
        return None
    if spent.group(1).lower() == "monthly":
        return _midnight(
            (now.date().replace(day=1) + timedelta(days=31)).replace(day=1)
        )
    return _midnight(now.date() + timedelta(days=1))


def _guard(host: str) -> None:
    blocked = _blocked.get(host)
    if blocked is None:
        return
    retry_at, reason = blocked
    if retry_at > datetime.now(UTC):
        raise QuotaExhausted(host, reason, retry_at)
    del _blocked[host]


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after", "").strip()
    if not value.isdigit():
        return None
    return min(float(value), MAX_BACKOFF_SECONDS)


def _reason(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("reason", "")) if isinstance(body, dict) else ""


def _backoff(attempt: int) -> float:
    delay: float = min(BACKOFF_SECONDS * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
    return delay * (0.5 + random.random() / 2)


def request(
    client: httpx.Client, method: str, url: str, cost: int = 1, **kwargs: Any
) -> httpx.Response:
    host = urlsplit(url).hostname or ""
    _guard(host)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        last = attempt == MAX_ATTEMPTS
        _throttle(url, cost)
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if last:
                raise
            delay = _backoff(attempt)
            logger.warning(
                "%s %s failed (%s), attempt %d/%d, waiting %.0fs",
                method,
                url,
                exc,
                attempt,
                MAX_ATTEMPTS,
                delay,
            )
        else:
            if response.status_code not in RETRY_STATUSES or last:
                response.raise_for_status()
                return response
            reason = _reason(response)
            spent = (
                _spent_until(reason, datetime.now(UTC))
                if response.status_code == 429
                else None
            )
            if spent is not None:
                _blocked[host] = (spent, reason)
                raise QuotaExhausted(host, reason, spent)
            delay = _retry_after(response) or _backoff(attempt)
            logger.warning(
                "%s %s returned %d%s, attempt %d/%d, waiting %.0fs",
                method,
                url,
                response.status_code,
                f" ({reason})" if reason else "",
                attempt,
                MAX_ATTEMPTS,
                delay,
            )
        time.sleep(delay)
    raise RuntimeError("unreachable")


def request_json(
    client: httpx.Client, method: str, url: str, cost: int = 1, **kwargs: Any
) -> dict[str, Any] | list[Any]:
    payload: dict[str, Any] | list[Any] = request(
        client, method, url, cost, **kwargs
    ).json()
    return payload


def request_text(client: httpx.Client, method: str, url: str, **kwargs: Any) -> str:
    return request(client, method, url, **kwargs).text
