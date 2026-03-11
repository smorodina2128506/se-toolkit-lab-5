"""Router for analytics endpoints.

Each endpoint performs SQL aggregation queries on the interaction data
populated by the ETL pipeline. All endpoints require a `lab` query
parameter to filter results by lab (e.g., "lab-01").
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, case, func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models.interaction import InteractionLog
from app.models.item import ItemRecord
from app.models.learner import Learner

router = APIRouter()

BUCKETS = ["0-25", "26-50", "51-75", "76-100"]


def _lab_title_filter(lab: str) -> str:
    """Transform lab-04 to 'Lab 04' for title matching."""
    parts = lab.split("-", 1)
    return f"Lab {parts[1]}" if len(parts) == 2 else lab


async def _get_lab_and_task_ids(session: AsyncSession, lab: str) -> tuple[int | None, list[int]]:
    """Find lab item by title and return (lab_id, [task_ids])."""
    title_part = _lab_title_filter(lab)
    lab_result = await session.exec(
        select(ItemRecord).where(
            ItemRecord.type == "lab",
            ItemRecord.title.contains(title_part),
        )
    )
    lab_item = lab_result.first()
    if not lab_item or not lab_item.id:
        return None, []
    task_result = await session.exec(
        select(ItemRecord).where(
            ItemRecord.type == "task",
            ItemRecord.parent_id == lab_item.id,
        )
    )
    task_ids = [t.id for t in task_result.all() if t.id is not None]
    return lab_item.id, task_ids


@router.get("/scores")
async def get_scores(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Score distribution histogram for a given lab.

    TODO: Implement this endpoint.
    - Find the lab item by matching title (e.g. "lab-04" → title contains "Lab 04")
    - Find all tasks that belong to this lab (parent_id = lab.id)
    - Query interactions for these items that have a score
    - Group scores into buckets: "0-25", "26-50", "51-75", "76-100"
      using CASE WHEN expressions
    - Return a JSON array:
      [{"bucket": "0-25", "count": 12}, {"bucket": "26-50", "count": 8}, ...]
    - Always return all four buckets, even if count is 0
    """
    _, task_ids = await _get_lab_and_task_ids(session, lab)
    if not task_ids:
        return [{"bucket": b, "count": 0} for b in BUCKETS]

    bucket_expr = case(
        (InteractionLog.score <= 25, "0-25"),
        (and_(InteractionLog.score > 25, InteractionLog.score <= 50), "26-50"),
        (and_(InteractionLog.score > 50, InteractionLog.score <= 75), "51-75"),
        (and_(InteractionLog.score > 75, InteractionLog.score <= 100), "76-100"),
    )
    stmt = (
        select(bucket_expr.label("bucket"), func.count().label("count"))
        .where(
            InteractionLog.item_id.in_(task_ids),
            InteractionLog.score.isnot(None),
        )
        .group_by(bucket_expr)
    )
    result = await session.exec(stmt)
    rows = {r.bucket: r.count for r in result.all()}
    return [{"bucket": b, "count": rows.get(b, 0)} for b in BUCKETS]


@router.get("/pass-rates")
async def get_pass_rates(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Per-task pass rates for a given lab.

    TODO: Implement this endpoint.
    - Find the lab item and its child task items
    - For each task, compute:
      - avg_score: average of interaction scores (round to 1 decimal)
      - attempts: total number of interactions
    - Return a JSON array:
      [{"task": "Repository Setup", "avg_score": 92.3, "attempts": 150}, ...]
    - Order by task title
    """
    _, task_ids = await _get_lab_and_task_ids(session, lab)
    if not task_ids:
        return []

    stmt = (
        select(
            ItemRecord.title.label("task"),
            func.avg(InteractionLog.score).label("avg_score"),
            func.count(InteractionLog.id).label("attempts"),
        )
        .select_from(InteractionLog)
        .join(ItemRecord, InteractionLog.item_id == ItemRecord.id)
        .where(
            InteractionLog.item_id.in_(task_ids),
            InteractionLog.score.isnot(None),
        )
        .group_by(ItemRecord.id, ItemRecord.title)
        .order_by(ItemRecord.title)
    )
    result = await session.exec(stmt)
    return [
        {"task": r.task, "avg_score": round(float(r.avg_score or 0), 1), "attempts": r.attempts}
        for r in result.all()
    ]


@router.get("/timeline")
async def get_timeline(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Submissions per day for a given lab.

    TODO: Implement this endpoint.
    - Find the lab item and its child task items
    - Group interactions by date (use func.date(created_at))
    - Count the number of submissions per day
    - Return a JSON array:
      [{"date": "2026-02-28", "submissions": 45}, ...]
    - Order by date ascending
    """
    _, task_ids = await _get_lab_and_task_ids(session, lab)
    if not task_ids:
        return []

    date_col = func.date(InteractionLog.created_at)
    stmt = (
        select(date_col.label("date"), func.count(InteractionLog.id).label("submissions"))
        .where(InteractionLog.item_id.in_(task_ids))
        .group_by(date_col)
        .order_by(date_col)
    )
    result = await session.exec(stmt)
    return [{"date": str(r.date), "submissions": r.submissions} for r in result.all()]


@router.get("/groups")
async def get_groups(
    lab: str = Query(..., description="Lab identifier, e.g. 'lab-01'"),
    session: AsyncSession = Depends(get_session),
):
    """Per-group performance for a given lab.

    TODO: Implement this endpoint.
    - Find the lab item and its child task items
    - Join interactions with learners to get student_group
    - For each group, compute:
      - avg_score: average score (round to 1 decimal)
      - students: count of distinct learners
    - Return a JSON array:
      [{"group": "B23-CS-01", "avg_score": 78.5, "students": 25}, ...]
    - Order by group name
    """
    _, task_ids = await _get_lab_and_task_ids(session, lab)
    if not task_ids:
        return []

    stmt = (
        select(
            Learner.student_group.label("group"),
            func.avg(InteractionLog.score).label("avg_score"),
            func.count(func.distinct(InteractionLog.learner_id)).label("students"),
        )
        .select_from(InteractionLog)
        .join(Learner, InteractionLog.learner_id == Learner.id)
        .where(InteractionLog.item_id.in_(task_ids))
        .group_by(Learner.student_group)
        .order_by(Learner.student_group)
    )
    result = await session.exec(stmt)
    return [
        {"group": r.group, "avg_score": round(float(r.avg_score or 0), 1), "students": r.students}
        for r in result.all()
    ]
