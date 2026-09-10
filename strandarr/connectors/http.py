import logging
import random
import re
import time
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 8
BACKOFF_SECONDS = 3
MAX_BACKOFF_SECONDS = 180

# What each host lets us spend per minute, in its own units. Open-Meteo's minutely ceiling of
# 600 counts *locations*, not requests -- measured: three 200-location requests in a row draw
# "Minutely API request limit exceeded" on the fourth, at exactly 600 locations. So a caller
# passes the number of locations as `cost` and the interval is derived, which keeps a wide
# request legal instead of leaving the retry loop to absorb a 429 it could have avoided.
# GFW has no published figure; 30/min reproduces the 2s spacing that stopped its 429s.
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
    """The upstream quota is spent for the day or month, so retrying now cannot help."""


# Minutely and hourly limits recover inside a retry window; daily and monthly ones do not.
# Retrying those burns all MAX_ATTEMPTS over ~20 minutes and fails the job anyway, so they
# are surfaced immediately with the quota named. Confirmed live: the archive host answers
# {"error":true,"reason":"Daily API request limit exceeded. Please try again tomorrow."}
# while the marine host, which has its own separate quota, still serves normally.
_SPENT_QUOTA = re.compile(r"\b(daily|monthly)\b", re.IGNORECASE)


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds the server asked us to wait, when it says so as a plain count."""
    value = response.headers.get("retry-after", "").strip()
    if not value.isdigit():
        return None
    return min(float(value), MAX_BACKOFF_SECONDS)


def _reason(response: httpx.Response) -> str:
    """Open-Meteo names the quota it refused on ("Minutely API request limit exceeded")."""
    try:
        body = response.json()
    except ValueError:
        return ""
    return str(body.get("reason", "")) if isinstance(body, dict) else ""


def _backoff(attempt: int) -> float:
    delay = min(BACKOFF_SECONDS * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS)
    return delay * (0.5 + random.random() / 2)


def request(
    client: httpx.Client, method: str, url: str, cost: int = 1, **kwargs
) -> httpx.Response:
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
            if response.status_code == 429 and _SPENT_QUOTA.search(reason):
                raise QuotaExhausted(f"{urlsplit(url).hostname}: {reason}")
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
    client: httpx.Client, method: str, url: str, cost: int = 1, **kwargs
) -> dict | list:
    return request(client, method, url, cost, **kwargs).json()


def request_text(client: httpx.Client, method: str, url: str, **kwargs) -> str:
    return request(client, method, url, **kwargs).text
