from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session as OrmSession

from strandarr.db.engine import Session

Window = tuple[datetime, datetime]


def session() -> Iterator[OrmSession]:
    with Session() as open_session:
        yield open_session


Db = Annotated[OrmSession, Depends(session)]
Hour = Annotated[datetime, Query()]
Day = Annotated[date, Query()]


def hour(at: datetime) -> datetime:
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.replace(minute=0, second=0, microsecond=0)


def hour_window(at: datetime) -> Window:
    start = hour(at)
    return start, start + timedelta(hours=1)
