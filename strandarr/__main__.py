import argparse
from datetime import date

from strandarr import log
from strandarr.repositories.db import Session
from strandarr.services import aisstream, reference, worker
from strandarr.services.schedule import DEFAULT_BACKFILL_DAYS, schedule_missing


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a date, expected YYYY-MM-DD (e.g. 2026-08-24)"
        ) from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="strandarr")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser(
        "ingest", help="enqueue jobs for days that are not stored yet"
    )
    ingest.add_argument(
        "--start",
        type=_date,
        metavar="YYYY-MM-DD",
        help=f"first day to ingest (default: {DEFAULT_BACKFILL_DAYS} days ago)",
    )
    ingest.add_argument(
        "--end",
        type=_date,
        metavar="YYYY-MM-DD",
        help="last day to ingest (default: today)",
    )
    ingest.add_argument(
        "--force",
        action="store_true",
        help="re-request days already stored and overwrite them",
    )

    commands.add_parser("worker", help="consume queued jobs until stopped")
    commands.add_parser("aisstream", help="record the live AIS feed until stopped")
    commands.add_parser(
        "reference",
        help="rebuild coastline segments and grid cells. Run before the first "
        "ingest, and again after changing GRID_STEP_DEG or "
        "MAX_DISTANCE_TO_COAST_KM",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    log.setup()

    if args.command == "ingest":
        with Session() as session:
            schedule_missing(session, start=args.start, end=args.end, force=args.force)
    elif args.command == "worker":
        worker.run_forever()
    elif args.command == "aisstream":
        aisstream.main()
    elif args.command == "reference":
        with Session() as session:
            written = reference.build(session)
        for name, count in written.items():
            print(f"upserted {count} {name}")


if __name__ == "__main__":
    main()
