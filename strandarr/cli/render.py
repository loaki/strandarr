from collections.abc import Mapping, Sequence
from datetime import date

from strandarr.services.audit import Summary


def reference(written: Mapping[str, int]) -> None:
    for name, count in written.items():
        print(f"upserted {count} {name}")


def coverage(summaries: Sequence[Summary], stored: Sequence[tuple[str, date]]) -> None:
    print(f"{'kind':<28}{'days':>7}{'full':>7}{'short':>7}{'cells':>8}  first..last")
    for summary in summaries:
        cells = f"{summary.expected_cells:.0f}" if summary.expected_cells else "-"
        print(
            f"{summary.kind:<28}{summary.days:>7}{summary.complete:>7}"
            f"{summary.incomplete:>7}{cells:>8}  {summary.first}..{summary.last}"
        )
        if summary.unmeasured:
            print(
                f"{'':<28}{summary.unmeasured} day(s) complete but never "
                f"counted, run coverage --rebuild"
            )
        for short in summary.worst:
            print(
                f"{'':<28}{short.day}  {short.row_count} row(s) "
                f"over {short.cell_count} cell(s)"
            )
    for source, last in stored:
        print(f"last stored {source}: {last}")
