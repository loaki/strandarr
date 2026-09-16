import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from strandarr import log
from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.cli import render
from strandarr.db.engine import unit_of_work
from strandarr.jobs import worker
from strandarr.jobs.schedule import schedule_missing
from strandarr.jobs.task import DEFAULT_BACKFILL_DAYS, MARINE_ARCHIVE_FIRST_DAY
from strandarr.services import aisstream, experiment, reference, validation

Setup = Callable[[argparse.ArgumentParser], None]
Run = Callable[[argparse.Namespace], None]
Scoped = Callable[[Session, argparse.Namespace], None]


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a date, expected YYYY-MM-DD (e.g. 2026-08-24)"
        ) from None


def _span(parser: argparse.ArgumentParser, start_help: str, end_help: str) -> None:
    parser.add_argument("--start", type=_date, metavar="YYYY-MM-DD", help=start_help)
    parser.add_argument("--end", type=_date, metavar="YYYY-MM-DD", help=end_help)


def _ingest_args(parser: argparse.ArgumentParser) -> None:
    _span(
        parser,
        f"first day to ingest (default: {DEFAULT_BACKFILL_DAYS} days ago)",
        "last day to ingest (default: today)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-request days already stored and overwrite them",
    )


def _scoring_args(parser: argparse.ArgumentParser) -> None:
    _span(
        parser,
        f"first day to score (default: {MARINE_ARCHIVE_FIRST_DAY})",
        "last day to score (default: today)",
    )
    parser.add_argument(
        "--min-release-days",
        type=int,
        default=MAX_DRIFT_DAYS,
        metavar="N",
        help=f"simulated release days required per scored day "
        f"(default: {MAX_DRIFT_DAYS})",
    )


def _scoped(run: Scoped) -> Run:
    def wrapped(args: argparse.Namespace) -> None:
        with unit_of_work() as session:
            run(session, args)

    return wrapped


def _ingest(session: Session, args: argparse.Namespace) -> None:
    schedule_missing(session, start=args.start, end=args.end, force=args.force)


def _validate(session: Session, args: argparse.Namespace) -> None:
    render.validation(
        validation.evaluate(
            session,
            start=args.start or MARINE_ARCHIVE_FIRST_DAY,
            end=args.end or date.today(),
            min_release_days=args.min_release_days,
        )
    )


def _experiment(session: Session, args: argparse.Namespace) -> None:
    render.search(
        experiment.dataset(
            session,
            start=args.start or MARINE_ARCHIVE_FIRST_DAY,
            end=args.end or date.today(),
            min_release_days=args.min_release_days,
        )
    )


def _reference(session: Session, args: argparse.Namespace) -> None:
    render.reference(reference.build(session))


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    run: Run
    setup: Setup | None = None


COMMANDS: tuple[Command, ...] = (
    Command(
        "ingest",
        "enqueue jobs for days that are not stored yet",
        _scoped(_ingest),
        _ingest_args,
    ),
    Command(
        "validate",
        "score drift predictions against recorded strandings",
        _scoped(_validate),
        _scoring_args,
    ),
    Command(
        "experiment",
        "search for a drift scoring recipe that beats climatology",
        _scoped(_experiment),
        _scoring_args,
    ),
    Command(
        "worker", "consume queued jobs until stopped", lambda _: worker.run_forever()
    ),
    Command(
        "aisstream",
        "record the live AIS feed until stopped",
        lambda _: aisstream.run_forever(),
    ),
    Command(
        "reference",
        "rebuild coastline segments and grid cells. Run before the first ingest, "
        "and again after changing GRID_STEP_DEG or MAX_DISTANCE_TO_COAST_KM",
        _scoped(_reference),
    ),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="strandarr")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in COMMANDS:
        sub = commands.add_parser(command.name, help=command.help)
        if command.setup is not None:
            command.setup(sub)
        sub.set_defaults(run=command.run)
    return parser


def main() -> None:
    args = _parser().parse_args()
    log.setup()
    try:
        args.run(args)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
