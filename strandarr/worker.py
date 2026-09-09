import logging
import time
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from strandarr import log, queue, store
from strandarr.connectors import gbif, gfw, open_meteo, pelagis_histocarto
from strandarr.db import Session as SessionFactory
from strandarr.models.current_observation import CurrentObservation
from strandarr.models.job import Job
from strandarr.models.stranding import Stranding
from strandarr.models.vessel_position import VesselPosition
from strandarr.models.wind_observation import WindObservation
from strandarr.schedule import BBOX, SOURCE_FISHING, grid_points, stored_days

logger = logging.getLogger(__name__)

POLL_SECONDS = 5


def _window(payload: dict) -> tuple[date, date]:
    return date.fromisoformat(payload["start"]), date.fromisoformat(payload["end"])


def _ingest_gfw(session: Session, payload: dict, dataset: str, source: str) -> int:
    start, end = _window(payload)
    skip = (
        set()
        if payload.get("force", False)
        else stored_days(session, VesselPosition, start, end, source)
    )
    if skip:
        logger.info("%s: %d day(s) already stored, not re-requested", source, len(skip))
    return _ingest_chunks(
        session,
        payload,
        VesselPosition,
        gfw.iter_positions(dataset, source, BBOX, start, end, skip),
    )


def _ingest_vessel_positions(session: Session, payload: dict) -> int:
    return _ingest_gfw(session, payload, gfw.FISHING_DATASET, SOURCE_FISHING)


def _ingest_chunks(session: Session, payload: dict, model: type, chunks) -> int:
    total = 0
    for rows in chunks:
        store.upsert(session, model, rows, update=payload.get("force", False))
        session.commit()
        total += len(rows)
    return total


def _sea_points(session: Session, start: date, end: date) -> list[tuple[float, float]]:
    """The marine model returns nothing on land, so current rows define the sea cells.
    Scoped to this window so a mask left over from a coarser grid cannot pin wind to it.
    """
    stored = set(
        session.execute(
            select(CurrentObservation.lat, CurrentObservation.lon)
            .where(
                CurrentObservation.recorded_at >= start,
                CurrentObservation.recorded_at < end + timedelta(days=1),
            )
            .distinct()
        ).all()
    )
    points = [point for point in grid_points() if point in stored]
    return points or grid_points()


def _ingest_wind(session: Session, payload: dict) -> int:
    start, end = _window(payload)
    points = _sea_points(session, start, end)
    logger.info("wind: %d of %d grid cells are sea", len(points), len(grid_points()))
    return _ingest_chunks(
        session, payload, WindObservation, open_meteo.iter_wind(points, start, end)
    )


def _ingest_currents(session: Session, payload: dict) -> int:
    start, end = _window(payload)
    return _ingest_chunks(
        session, payload, CurrentObservation, open_meteo.iter_currents(grid_points(), start, end)
    )


def _ingest_strandings(session: Session, payload: dict) -> int:
    start, end = _window(payload)
    rows = gbif.fetch_strandings(BBOX, start, end)
    store.upsert(session, Stranding, rows, update=payload.get("force", False))
    return len(rows)


def _ingest_strandings_histocarto(session: Session, payload: dict) -> int:
    start, end = _window(payload)
    rows = pelagis_histocarto.fetch_strandings(BBOX, start, end)
    store.upsert(session, Stranding, rows, update=payload.get("force", False))
    return len(rows)


JOB_HANDLERS: dict[str, Callable[[Session, dict], int]] = {
    "ingest_vessel_positions": _ingest_vessel_positions,
    "ingest_wind": _ingest_wind,
    "ingest_currents": _ingest_currents,
    "ingest_strandings": _ingest_strandings,
    "ingest_strandings_histocarto": _ingest_strandings_histocarto,
}


def run_job(session: Session, job: Job) -> int:
    handler = JOB_HANDLERS.get(job.kind)
    if handler is None:
        raise ValueError(f"unknown job kind: {job.kind}")
    rows = handler(session, job.payload)
    session.commit()
    return rows


def run_pending(session: Session) -> int:
    processed = 0
    while True:
        job = queue.claim_next(session)
        if job is None:
            return processed
        logger.info("job %d %s starting %s", job.id, job.kind, job.payload)
        started = time.monotonic()
        try:
            rows = run_job(session, job)
        except Exception as exc:
            session.rollback()
            queue.mark_failed(session, job, str(exc))
            logger.error("job %d %s failed: %s", job.id, job.kind, exc)
        else:
            queue.mark_done(session, job)
            logger.info(
                "job %d %s done: %d rows in %.1fs",
                job.id,
                job.kind,
                rows,
                time.monotonic() - started,
            )
        processed += 1


def run_forever() -> None:
    logger.info("worker started, polling every %ds", POLL_SECONDS)
    with SessionFactory() as session:
        queue.release_running(session)
    while True:
        with SessionFactory() as session:
            processed = run_pending(session)
        if not processed:
            time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    log.setup()
    run_forever()
