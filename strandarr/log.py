import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

FORMAT = "%(asctime)s %(levelname)-5s %(name)s %(job)s%(message)s"

_job: ContextVar[int | None] = ContextVar("job", default=None)


@contextmanager
def job(job_id: int) -> Iterator[None]:
    token = _job.set(job_id)
    try:
        yield
    finally:
        _job.reset(token)


class JobFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        job_id = _job.get()
        record.job = "" if job_id is None else f"job {job_id} "
        return super().format(record)


def setup() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JobFormatter(FORMAT, datefmt="%H:%M:%S"))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    logging.getLogger("httpx").setLevel(logging.WARNING)
