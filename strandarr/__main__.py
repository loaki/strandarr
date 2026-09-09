import argparse
from datetime import date

from strandarr import log
from strandarr.db import Session
from strandarr.schedule import DEFAULT_BACKFILL_DAYS, schedule_missing
from strandarr.worker import run_forever


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a date, expected YYYY-MM-DD (e.g. 2026-08-24)"
        ) from None


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="strandarr",
        description="ingest: enqueue jobs for missing data. worker: consume jobs.",
    )
    parser.add_argument("command", choices=["ingest", "worker"])
    parser.add_argument(
        "--start",
        type=_date,
        metavar="YYYY-MM-DD",
        help=f"first day to ingest, e.g. 2026-08-24 (default: {DEFAULT_BACKFILL_DAYS} days ago)",
    )
    parser.add_argument(
        "--end",
        type=_date,
        metavar="YYYY-MM-DD",
        help="last day to ingest, e.g. 2026-09-02 (default: today)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="re-request days already stored and overwrite them (default: skip stored days)",
    )
    args = parser.parse_args()

    log.setup()

    if args.command == "ingest":
        with Session() as session:
            schedule_missing(session, start=args.start, end=args.end, force=args.force)
    elif args.command == "worker":
        run_forever()


if __name__ == "__main__":
    main()
