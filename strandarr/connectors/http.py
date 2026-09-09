import logging
import time

import httpx

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 5
BACKOFF_SECONDS = 3


def request(client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        last = attempt == MAX_ATTEMPTS
        try:
            response = client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            if last:
                raise
            logger.warning(
                "%s %s failed (%s), attempt %d/%d", method, url, exc, attempt, MAX_ATTEMPTS
            )
        else:
            if response.status_code not in RETRY_STATUSES or last:
                response.raise_for_status()
                return response
            logger.warning(
                "%s %s returned %d, attempt %d/%d",
                method,
                url,
                response.status_code,
                attempt,
                MAX_ATTEMPTS,
            )
        time.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
    raise RuntimeError("unreachable")


def request_json(client: httpx.Client, method: str, url: str, **kwargs) -> dict | list:
    return request(client, method, url, **kwargs).json()


def request_text(client: httpx.Client, method: str, url: str, **kwargs) -> str:
    return request(client, method, url, **kwargs).text
