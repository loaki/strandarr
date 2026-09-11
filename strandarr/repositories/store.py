from collections.abc import Sequence
from typing import cast

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from strandarr.models.base import Base

MAX_QUERY_PARAMS = 60000

SKIP_COLUMNS = frozenset({"id", "created_at"})


def upsert(
    session: Session,
    model: type[Base],
    rows: Sequence[Base],
    overwrite: bool = False,
) -> int:
    if not rows:
        return 0
    table = cast(Table, model.__table__)
    columns = [c.name for c in table.columns if c.name not in SKIP_COLUMNS]
    conflict = conflict_columns(model)

    deduped = {
        tuple(getattr(row, name) for name in conflict): {
            name: getattr(row, name) for name in columns
        }
        for row in rows
    }
    values = list(deduped.values())

    max_params_per_row = len(table.columns)
    batch_size = max(1, MAX_QUERY_PARAMS // max_params_per_row)
    for start in range(0, len(values), batch_size):
        statement = insert(model).values(values[start : start + batch_size])
        if overwrite:
            statement = statement.on_conflict_do_update(
                index_elements=conflict,
                set_={
                    name: statement.excluded[name]
                    for name in columns
                    if name not in conflict
                },
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=conflict)
        session.execute(statement)
    return len(values)


def conflict_columns(model: type[Base]) -> list[str]:
    table = cast(Table, model.__table__)
    unique = sorted(
        (index for index in table.indexes if index.unique), key=lambda i: i.name or ""
    )
    if len(unique) != 1:
        raise ValueError(
            f"{model.__name__} needs exactly one unique index to upsert on, "
            f"found {len(unique)}"
        )
    return [column.name for column in unique[0].columns]
