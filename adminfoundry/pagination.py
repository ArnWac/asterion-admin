import math
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def paginate(db: AsyncSession, stmt, page: int, page_size: int):
    """Single round-trip pagination using COUNT() OVER window function.

    Falls back to a separate COUNT only when the requested page is beyond the
    result set (offset > 0 and rows empty — rare edge case).
    """
    offset = (page - 1) * page_size
    count_col = func.count().over().label("_total")
    rows = (await db.execute(
        stmt.add_columns(count_col).offset(offset).limit(page_size)
    )).all()

    if rows:
        total = rows[0]._total
        items = [r[0] for r in rows]
    elif offset == 0:
        total = 0
        items = []
    else:
        # Requested page is beyond the data — separate count for correct total
        total = (await db.execute(
            select(func.count()).select_from(stmt.subquery())
        )).scalar_one()
        items = []

    pages = math.ceil(total / page_size) if total else 0
    return items, total, pages
