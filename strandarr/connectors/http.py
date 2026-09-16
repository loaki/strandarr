import logging
import random
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx

from strandarr.analysis.timeframe import midnight

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 8
BACKOFF_SECONDS = 3
MAX_BACKOFF_SECONDS = 180

MINUTE = 60.0
HOUR = 3600.0

OPEN_METEO_BUDGETS = ((MINUTE, 600), (HOUR, 5000))

BUDGETS: dict[str, tuple[tuple[float, int], ...]] = {
    "archive-api.open-meteo.com": OPEN_METEO_BUDGETS,
    "marine-api.open-meteo.com": OPEN_METEO_BUDGETS,
    "api.open-meteo.com": OPEN_METEO_BUDGETS,
    "gateway.api.globalfishingwatch.org": ((MINUTE, 30),),
}

_SPENT_QUOTA = re.compile(r"\b(hourly|daily|monthly)\b", re.IGNORECASE)


def backoff(
    attempt: int, base: float = BACKOFF_SECONDS, cap: float = MAX_BACKOFF_SECONDS
) -> float:
    delay: float = min(base * 2 ** (attempt - 1), cap)
    return delay * (0.5 + random.random() / 2)


class QuotaExhausted(RuntimeError):
    def __init__(self, host: str, reason: str, retry_at: datetime) -> None:
        super().__init__(f"{host}: {reason}")
        self.retry_at = retry_at


def _spent_until(reason: str, now: datetime) -> datetime | None:
    spent = _SPENT_QUOTA.search(reason)
    if spent is None:
        return None
    window = spent.group(1).lower()
    if window == "hourly":
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    if window == "monthly":
        return midnight((now.date().replace(day=1) + timedelta(days=31)).replace(day=1))
    return midnight(now.date() + timedelta(days=1))


class Throttle:
    def __init__(self, budgets: dict[str, tuple[tuple[float, int], ...]]) -> None:
        self.budgets = budgets
        self.sent_at: dict[str, float] = {}
        self.blocked: dict[str, tuple[datetime, str]] = {}

    def guard(self, host: str) -> None:
        blocked = self.blocked.get(host)
        if blocked is None:
            return
        retry_at, reason = blocked
        if retry_at > datetime.now(UTC):
            raise QuotaExhausted(host, reason, retry_at)
        del self.blocked[host]

    def block(self, host: str, reason: str, until: datetime) -> QuotaExhausted:
        self.blocked[host] = (until, reason)
        return QuotaExhausted(host, reason, until)

    def wait(self, host: str, cost: int) -> None:
        budgets = self.budgets.get(host)
        if budgets is None:
            return
        interval = max(window * cost / units for window, units in budgets)
        delay = interval - (time.monotonic() - self.sent_at.get(host, 0.0))
        if delay > 0:
            logger.debug("%s: spacing %d unit(s), waiting %.1fs", host, cost, delay)
            time.sleep(delay)
        self.sent_at[host] = time.monotonic()


throttle = Throttle(BUDGETS)


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


def request(
    client: httpx.Client, method: str, url: str, cost: int = 1, **kwargs: Any
) -> httpx.Response:
    host = urlsplit(url).hostname or ""
    throttle.guard(host)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        last = attempt == MAX_ATTEMPTS
        throttle.wait(host, cost)
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if last:
                raise
            delay = backoff(attempt)
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
                raise throttle.block(host, reason, spent)
            delay = _retry_after(response) or backoff(attempt)
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


def request_object(
    client: httpx.Client, method: str, url: str, label: str, **kwargs: Any
) -> dict[str, Any]:
    payload = request_json(client, method, url, **kwargs)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label}: expected an object, got {type(payload).__name__}")
    return payload
