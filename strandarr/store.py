from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from strandarr.models.base import Base

MAX_QUERY_PARAMS = 60000


def _conflict_columns(model: type[Base]) -> list[str]:
    for index in model.__table__.indexes:
        if index.unique:
            return [column.name for column in index.columns]
    raise ValueError(f"{model.__name__} has no unique index to upsert on")


def upsert(session: Session, model: type[Base], rows: list[Base], update: bool = False) -> None:
    if not rows:
        return
    columns = [c.name for c in model.__table__.columns if c.name not in ("id", "created_at")]
    conflict = _conflict_columns(model)

    deduped = {
        tuple(getattr(row, name) for name in conflict): {
            name: getattr(row, name) for name in columns
        }
        for row in rows
    }
    values = list(deduped.values())

    batch_size = max(1, MAX_QUERY_PARAMS // len(model.__table__.columns))
    for start in range(0, len(values), batch_size):
        stmt = insert(model).values(values[start : start + batch_size])
        if update:
            stmt = stmt.on_conflict_do_update(
                index_elements=conflict,
                set_={name: stmt.excluded[name] for name in columns if name not in conflict},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict)
        session.execute(stmt)
