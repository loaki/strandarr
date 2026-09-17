import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from strandarr import jobs, log
from strandarr.analysis import risk
from strandarr.db import unit_of_work
from strandarr.models import Job, JobStatus
from strandarr.sources import aisstream, coastline

Run = Callable[[argparse.Namespace], None]
Scoped = Callable[[Session, argparse.Namespace], None]


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a date, expected YYYY-MM-DD"
        ) from None


def _scoped(run: Scoped) -> Run:
    def wrapped(args: argparse.Namespace) -> None:
        with unit_of_work() as session:
            run(session, args)

    return wrapped


def _schedule_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--start",
        type=_date,
        metavar="YYYY-MM-DD",
        help=f"first day to cover (default: {jobs.DEFAULT_BACKFILL_DAYS} days ago). "
        "Set it to backfill history",
    )
    parser.add_argument(
        "--end", type=_date, metavar="YYYY-MM-DD", help="last day to cover"
    )


def _schedule(session: Session, args: argparse.Namespace) -> None:
    jobs.schedule(session, start=args.start, end=args.end)


def _retry_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kind", choices=sorted(jobs.TASKS), help="only this kind")


def _retry(session: Session, args: argparse.Namespace) -> None:
    jobs.retry(session, args.kind)


def _status(session: Session, args: argparse.Namespace) -> None:
    rows = session.execute(
        select(
            Job.kind,
            Job.status,
            func.count(),
            func.min(Job.day),
            func.max(Job.day),
        ).group_by(Job.kind, Job.status)
    ).all()
    counts: dict[str, dict[str, int]] = {}
    span: dict[str, tuple[date, date]] = {}
    for kind, status, count, first, last in rows:
        counts.setdefault(kind, {})[str(status)] = count
        seen = span.get(kind)
        span[kind] = (
            first if seen is None else min(seen[0], first),
            last if seen is None else max(seen[1], last),
        )
    states = [str(status) for status in JobStatus]
    header = "".join(f"{state:>9}" for state in states)
    print(f"{'kind':<20}{header}  first..last")
    for kind in sorted(counts):
        line = "".join(f"{counts[kind].get(state, 0):>9}" for state in states)
        first, last = span[kind]
        print(f"{kind:<20}{line}  {first}..{last}")

    stuck = session.execute(
        select(Job.kind, Job.day, Job.error)
        .where(Job.status == JobStatus.FAILED)
        .order_by(Job.day.desc())
        .limit(5)
    ).all()
    for kind, day, error in stuck:
        print(f"  failed {kind} {day}: {(error or '').splitlines()[:1]}")


def _reference(session: Session, args: argparse.Namespace) -> None:
    coastline.build(session)


def _fit(session: Session, args: argparse.Namespace) -> None:
    risk.fit(session)
    print(f"wrote {risk.COEFFICIENTS_PATH}")
    print("re-run the risk jobs to apply it: strandarr schedule --start <first day>")


def _skill(session: Session, args: argparse.Namespace) -> None:
    for name, value in risk.skill(session).items():
        print(f"{name:<12}{value:.4f}")


@dataclass(frozen=True)
class Command:
    name: str
    help: str
    run: Run
    setup: Callable[[argparse.ArgumentParser], None] | None = None


COMMANDS: tuple[Command, ...] = (
    Command(
        "schedule",
        "queue the days that are not covered yet (this is what cron runs)",
        _scoped(_schedule),
        _schedule_args,
    ),
    Command("work", "consume queued jobs until stopped", lambda _: jobs.run_forever()),
    Command(
        "aisstream",
        "record the live AIS feed until stopped",
        lambda _: aisstream.run_forever(),
    ),
    Command("status", "show what the queue holds", _scoped(_status)),
    Command("retry", "re-queue failed jobs", _scoped(_retry), _retry_args),
    Command(
        "reference",
        "rebuild the coastal segments. Run once before the first schedule",
        _scoped(_reference),
    ),
    Command(
        "fit",
        "refit the risk coefficients from stored segment-days and strandings",
        _scoped(_fit),
    ),
    Command(
        "skill", "score the stored predictions against what happened", _scoped(_skill)
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
