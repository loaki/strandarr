import argparse
from datetime import date

from strandarr import log
from strandarr.analysis.drift import MAX_DRIFT_DAYS
from strandarr.repositories.db import Session
from strandarr.services import aisstream, experiment, reference, validation, worker
from strandarr.services.schedule import (
    DEFAULT_BACKFILL_DAYS,
    MARINE_ARCHIVE_FIRST_DAY,
    schedule_missing,
)


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

    validate = commands.add_parser(
        "validate",
        help="score drift predictions against recorded strandings",
    )
    validate.add_argument(
        "--start",
        type=_date,
        metavar="YYYY-MM-DD",
        help=f"first day to score (default: {MARINE_ARCHIVE_FIRST_DAY})",
    )
    validate.add_argument(
        "--end",
        type=_date,
        metavar="YYYY-MM-DD",
        help="last day to score (default: today)",
    )
    validate.add_argument(
        "--min-release-days",
        type=int,
        default=MAX_DRIFT_DAYS,
        metavar="N",
        help=f"simulated release days required per scored day "
        f"(default: {MAX_DRIFT_DAYS})",
    )

    explore = commands.add_parser(
        "experiment",
        help="search for a drift scoring recipe that beats climatology",
    )
    explore.add_argument(
        "--start",
        type=_date,
        metavar="YYYY-MM-DD",
        help=f"first day to score (default: {MARINE_ARCHIVE_FIRST_DAY})",
    )
    explore.add_argument(
        "--end",
        type=_date,
        metavar="YYYY-MM-DD",
        help="last day to score (default: today)",
    )
    explore.add_argument(
        "--min-release-days",
        type=int,
        default=MAX_DRIFT_DAYS,
        metavar="N",
        help=f"simulated release days required per scored day "
        f"(default: {MAX_DRIFT_DAYS})",
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


COLUMNS = (
    "days",
    "obs",
    "cells",
    "animals",
    "auc",
    *(f"top{k}" for k in validation.TOP_K),
)


def _print(result: validation.Validation) -> None:
    print(
        f"{result.start}..{result.end}  {result.segments} segment(s), "
        f"{result.eligible_days} day(s) scorable, {result.matched} stranding(s) "
        f"snapped across all years ({result.unmatched} beyond "
        f"{validation.SNAP_RADIUS_KM:.0f} km, dropped)"
    )
    print(f"{'group':<18}{'score':<12}" + "".join(f"{name:>9}" for name in COLUMNS))
    for report in result.reports:
        for name, measured in (
            ("drift", report.model),
            ("climatology", report.baseline),
        ):
            auc = "-" if measured.auc is None else f"{measured.auc:.3f}"
            print(
                f"{report.label:<18}{name:<12}"
                f"{measured.days:>9}{report.records:>9}{measured.positives:>9}"
                f"{report.individuals:>9}{auc:>9}"
                + "".join(f"{measured.capture[k]:>9.3f}" for k in validation.TOP_K)
            )


def _print_search(data: experiment.Dataset) -> None:
    mean, share, covered = experiment.sparsity(data)
    print(
        f"{len(data.days)} scorable day(s) with strandings, {data.size} segments\n"
        f"drift is non-zero on {mean:.0f} segment(s) per day ({share:.1f} % of the "
        f"coast); {covered:.1f} % of strandings fall on a segment drift scored "
        f"above zero"
    )
    header = f"{'recipe':<28}{'auc':>8}" + "".join(
        f"{f'top{k}':>8}" for k in experiment.TOP_K
    )
    for title, stage in experiment.search(data):
        print(f"\n== {title}")
        print(header)
        for variant, measured in stage:
            auc = "-" if measured.auc is None else f"{measured.auc:.3f}"
            print(
                f"{variant.label:<28}{auc:>8}"
                + "".join(f"{measured.capture[k]:>8.3f}" for k in experiment.TOP_K)
            )


def main() -> None:
    args = _parser().parse_args()
    log.setup()
    try:
        _run(args)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


def _run(args: argparse.Namespace) -> None:
    if args.command == "ingest":
        with Session() as session:
            schedule_missing(session, start=args.start, end=args.end, force=args.force)
    elif args.command == "worker":
        worker.run_forever()
    elif args.command == "aisstream":
        aisstream.main()
    elif args.command == "validate":
        with Session() as session:
            _print(
                validation.evaluate(
                    session,
                    start=args.start or MARINE_ARCHIVE_FIRST_DAY,
                    end=args.end or date.today(),
                    min_release_days=args.min_release_days,
                )
            )
    elif args.command == "experiment":
        with Session() as session:
            _print_search(
                experiment.load(
                    session,
                    start=args.start or MARINE_ARCHIVE_FIRST_DAY,
                    end=args.end or date.today(),
                    min_release_days=args.min_release_days,
                )
            )
    elif args.command == "reference":
        with Session() as session:
            written = reference.build(session)
        for name, count in written.items():
            print(f"upserted {count} {name}")


if __name__ == "__main__":
    main()
