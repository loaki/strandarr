from collections.abc import Sequence
from typing import cast

from sqlalchemy import ColumnExpressionArgument, Table, delete
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
    conflict = model.__conflict__
    if not conflict:
        raise ValueError(f"{model.__name__} declares no __conflict__ to upsert on")

    table = cast(Table, model.__table__)
    columns = [c.name for c in table.columns if c.name not in SKIP_COLUMNS]
    deduped = {
        tuple(getattr(row, name) for name in conflict): {
            name: getattr(row, name) for name in columns
        }
        for row in rows
    }
    values = list(deduped.values())

    batch_size = max(1, MAX_QUERY_PARAMS // len(table.columns))
    for start in range(0, len(values), batch_size):
        statement = insert(model).values(values[start : start + batch_size])
        if overwrite:
            statement = statement.on_conflict_do_update(
                index_elements=list(conflict),
                set_={
                    name: statement.excluded[name]
                    for name in columns
                    if name not in conflict
                },
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=list(conflict))
        session.execute(statement)
    return len(values)


def replace(
    session: Session,
    model: type[Base],
    rows: Sequence[Base],
    *scope: ColumnExpressionArgument[bool],
) -> int:
    session.execute(delete(model).where(*scope))
    return upsert(session, model, rows, overwrite=True)
