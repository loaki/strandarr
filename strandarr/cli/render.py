from collections.abc import Mapping

from strandarr.services import experiment
from strandarr.services.dataset import TOP_K, Dataset
from strandarr.services.observations import SNAP_RADIUS_KM
from strandarr.services.validation import Validation

COLUMNS = ("days", "obs", "cells", "animals", "auc", *(f"top{k}" for k in TOP_K))


def _auc(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def validation(result: Validation) -> None:
    print(
        f"{result.days.start}..{result.days.end}  {result.segments} segment(s), "
        f"{result.eligible_days} day(s) scorable, {result.matched} stranding(s) "
        f"snapped across all years ({result.unmatched} beyond "
        f"{SNAP_RADIUS_KM:.0f} km, dropped)"
    )
    print(f"{'group':<18}{'score':<12}" + "".join(f"{name:>9}" for name in COLUMNS))
    for report in result.reports:
        for name, measured in (
            ("drift", report.model),
            ("climatology", report.baseline),
        ):
            print(
                f"{report.label:<18}{name:<12}"
                f"{measured.days:>9}{report.records:>9}{measured.positives:>9}"
                f"{report.individuals:>9}{_auc(measured.auc):>9}"
                + "".join(f"{measured.capture[k]:>9.3f}" for k in TOP_K)
            )


def search(data: Dataset) -> None:
    mean, share, covered = experiment.sparsity(data)
    print(
        f"{len(data.days)} scorable day(s) with strandings, {data.size} segments\n"
        f"drift is non-zero on {mean:.0f} segment(s) per day ({share:.1f} % of the "
        f"coast); {covered:.1f} % of strandings fall on a segment drift scored "
        f"above zero"
    )
    header = f"{'recipe':<28}{'auc':>8}" + "".join(f"{f'top{k}':>8}" for k in TOP_K)
    for title, stage in experiment.search(data):
        print(f"\n== {title}")
        print(header)
        for variant, measured in stage:
            print(
                f"{variant.label:<28}{_auc(measured.auc):>8}"
                + "".join(f"{measured.capture[k]:>8.3f}" for k in TOP_K)
            )


def reference(written: Mapping[str, int]) -> None:
    for name, count in written.items():
        print(f"upserted {count} {name}")
