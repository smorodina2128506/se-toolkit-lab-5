"""ETL pipeline: fetch data from the autochecker API and load it into the database.

The autochecker dashboard API provides two endpoints:
- GET /api/items — lab/task catalog
- GET /api/logs  — anonymized check results (supports ?since= and ?limit= params)

Both require HTTP Basic Auth (email + password from settings).
"""

from datetime import datetime

import httpx
from sqlmodel.ext.asyncio.session import AsyncSession

from app.settings import settings


# ---------------------------------------------------------------------------
# Extract — fetch data from the autochecker API
# ---------------------------------------------------------------------------


async def fetch_items() -> list[dict]:
    """Fetch the lab/task catalog from the autochecker API.

    TODO: Implement this function.
    - Use httpx.AsyncClient to GET {settings.autochecker_api_url}/api/items
    - Pass HTTP Basic Auth using settings.autochecker_email and
      settings.autochecker_password
    - The response is a JSON array of objects with keys:
      lab (str), task (str | null), title (str), type ("lab" | "task")
    - Return the parsed list of dicts
    - Raise an exception if the response status is not 200
    """
    url = f"{settings.autochecker_api_url.rstrip('/')}/api/items"
    auth = httpx.BasicAuth(
        settings.autochecker_email,
        settings.autochecker_password,
    )
    async with httpx.AsyncClient() as client:
        response = await client.get(url, auth=auth)
        response.raise_for_status()
        return response.json()


async def fetch_logs(since: datetime | None = None) -> list[dict]:
    """Fetch check results from the autochecker API.

    TODO: Implement this function.
    - Use httpx.AsyncClient to GET {settings.autochecker_api_url}/api/logs
    - Pass HTTP Basic Auth using settings.autochecker_email and
      settings.autochecker_password
    - Query parameters:
      - limit=500 (fetch in batches)
      - since={iso timestamp} if provided (for incremental sync)
    - The response JSON has shape:
      {"logs": [...], "count": int, "has_more": bool}
    - Handle pagination: keep fetching while has_more is True
      - Use the submitted_at of the last log as the new "since" value
    - Return the combined list of all log dicts from all pages
    """
    url = f"{settings.autochecker_api_url.rstrip('/')}/api/logs"
    auth = httpx.BasicAuth(
        settings.autochecker_email,
        settings.autochecker_password,
    )
    all_logs: list[dict] = []
    since_param = since.isoformat() if since else None

    async with httpx.AsyncClient() as client:
        while True:
            params: dict[str, str | int] = {"limit": 500}
            if since_param:
                params["since"] = since_param

            response = await client.get(url, auth=auth, params=params)
            response.raise_for_status()
            data = response.json()

            logs = data.get("logs", [])
            all_logs.extend(logs)

            if not data.get("has_more", False):
                break

            if not logs:
                break

            last_submitted = logs[-1].get("submitted_at")
            if last_submitted:
                since_param = last_submitted
            else:
                break

    return all_logs


# ---------------------------------------------------------------------------
# Load — insert fetched data into the local database
# ---------------------------------------------------------------------------


async def load_items(items: list[dict], session: AsyncSession) -> int:
    """Load items (labs and tasks) into the database.

    TODO: Implement this function.
    - Import ItemRecord from app.models.item
    - Process labs first (items where type="lab"):
      - For each lab, check if an item with type="lab" and matching title
        already exists (SELECT)
      - If not, INSERT a new ItemRecord(type="lab", title=lab_title)
      - Build a dict mapping the lab's short ID (the "lab" field, e.g.
        "lab-01") to the lab's database record, so you can look up
        parent IDs when processing tasks
    - Then process tasks (items where type="task"):
      - Find the parent lab item using the task's "lab" field (e.g.
        "lab-01") as the key into the dict you built above
      - Check if a task with this title and parent_id already exists
      - If not, INSERT a new ItemRecord(type="task", title=task_title,
        parent_id=lab_item.id)
    - Commit after all inserts
    - Return the number of newly created items
    """
    from sqlmodel import col, select

    from app.models.item import ItemRecord

    created = 0
    lab_by_short_id: dict[str, ItemRecord] = {}

    labs = [i for i in items if i.get("type") == "lab"]
    for item in labs:
        lab_id = item.get("lab", "")
        title = item.get("title", "")
        result = await session.exec(
            select(ItemRecord).where(
                col(ItemRecord.type) == "lab", col(ItemRecord.title) == title
            )
        )
        existing = result.first()
        if existing:
            lab_by_short_id[lab_id] = existing
        else:
            record = ItemRecord(type="lab", title=title)
            session.add(record)
            await session.flush()
            await session.refresh(record)
            lab_by_short_id[lab_id] = record
            created += 1

    tasks = [i for i in items if i.get("type") == "task"]
    for item in tasks:
        lab_id = item.get("lab", "")
        title = item.get("title", "")
        parent = lab_by_short_id.get(lab_id)
        if not parent or not parent.id:
            continue
        result = await session.exec(
            select(ItemRecord).where(
                col(ItemRecord.type) == "task",
                col(ItemRecord.title) == title,
                col(ItemRecord.parent_id) == parent.id,
            )
        )
        if result.first() is None:
            record = ItemRecord(type="task", title=title, parent_id=parent.id)
            session.add(record)
            created += 1

    await session.commit()
    return created


async def load_logs(
    logs: list[dict], items_catalog: list[dict], session: AsyncSession
) -> int:
    """Load interaction logs into the database.

    Args:
        logs: Raw log dicts from the API (each has lab, task, student_id, etc.)
        items_catalog: Raw item dicts from fetch_items() — needed to map
            short IDs (e.g. "lab-01", "setup") to item titles stored in the DB.
        session: Database session.

    TODO: Implement this function.
    - Import Learner from app.models.learner
    - Import InteractionLog from app.models.interaction
    - Import ItemRecord from app.models.item
    - Build a lookup from (lab_short_id, task_short_id) to item title
      using items_catalog. For labs, the key is (lab, None). For tasks,
      the key is (lab, task). The value is the item's title.
    - For each log dict:
      1. Find or create a Learner by external_id (log["student_id"])
         - If creating, set student_group from log["group"]
      2. Find the matching item in the database:
         - Use the lookup to get the title for (log["lab"], log["task"])
         - Query the DB for an ItemRecord with that title
         - Skip this log if no matching item is found
      3. Check if an InteractionLog with this external_id already exists
         (for idempotent upsert — skip if it does)
      4. Create InteractionLog with:
         - external_id = log["id"]
         - learner_id = learner.id
         - item_id = item.id
         - kind = "attempt"
         - score = log["score"]
         - checks_passed = log["passed"]
         - checks_total = log["total"]
         - created_at = parsed log["submitted_at"]
    - Commit after all inserts
    - Return the number of newly created interactions
    """
    from datetime import datetime, timezone

    from sqlmodel import col, select

    from app.models.interaction import InteractionLog
    from app.models.item import ItemRecord
    from app.models.learner import Learner

    title_lookup: dict[tuple[str, str | None], str] = {}
    for item in items_catalog:
        lab_id = item.get("lab", "")
        task_id = item.get("task")
        title = item.get("title", "")
        key = (lab_id, task_id)
        title_lookup[key] = title

    created = 0
    learner_cache: dict[str, Learner] = {}
    item_cache: dict[tuple[str, str | None], ItemRecord | None] = {}

    for log in logs:
        student_id = str(log.get("student_id", ""))
        if not student_id:
            continue

        if student_id not in learner_cache:
            result = await session.exec(
                select(Learner).where(col(Learner.external_id) == student_id)
            )
            existing = result.first()
            if existing:
                learner_cache[student_id] = existing
            else:
                learner = Learner(
                    external_id=student_id,
                    student_group=str(log.get("group", "")),
                    enrolled_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                session.add(learner)
                await session.flush()
                await session.refresh(learner)
                learner_cache[student_id] = learner
        learner = learner_cache[student_id]
        if not learner.id:
            continue

        lab_id = log.get("lab", "")
        task_id = log.get("task")
        key = (lab_id, task_id)
        title = title_lookup.get(key)
        if not title:
            continue

        if key not in item_cache:
            if task_id is None:
                result = await session.exec(
                    select(ItemRecord).where(
                        col(ItemRecord.type) == "lab",
                        col(ItemRecord.title) == title,
                    )
                )
            else:
                lab_title = title_lookup.get((lab_id, None))
                if not lab_title:
                    continue
                lab_result = await session.exec(
                    select(ItemRecord).where(
                        col(ItemRecord.type) == "lab",
                        col(ItemRecord.title) == lab_title,
                    )
                )
                lab_record = lab_result.first()
                if not lab_record or not lab_record.id:
                    continue
                result = await session.exec(
                    select(ItemRecord).where(
                        col(ItemRecord.type) == "task",
                        col(ItemRecord.title) == title,
                        col(ItemRecord.parent_id) == lab_record.id,
                    )
                )
            item_record = result.first()
            item_cache[key] = item_record if item_record else None
        item_record = item_cache[key]
        if not item_record or not item_record.id:
            continue

        external_id = log.get("id")
        if external_id is None:
            continue
        ext_id_int = (
            int(external_id) if not isinstance(external_id, int) else external_id
        )

        result = await session.exec(
            select(InteractionLog).where(col(InteractionLog.external_id) == ext_id_int)
        )
        if result.first() is not None:
            continue

        submitted_at_str = log.get("submitted_at")
        created_at = datetime.now()
        if submitted_at_str:
            try:
                created_at = datetime.fromisoformat(
                    submitted_at_str.replace("Z", "+00:00")
                ).replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        interaction = InteractionLog(
            external_id=ext_id_int,
            learner_id=learner.id,
            item_id=item_record.id,
            kind="attempt",
            score=float(log["score"]) if log.get("score") is not None else None,
            checks_passed=log.get("passed"),
            checks_total=log.get("total"),
            created_at=created_at,
        )
        session.add(interaction)
        created += 1

    await session.commit()
    return created


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def sync(session: AsyncSession) -> dict:
    """Run the full ETL pipeline.

    TODO: Implement this function.
    - Step 1: Fetch items from the API (keep the raw list) and load them
      into the database
    - Step 2: Determine the last synced timestamp
      - Query the most recent created_at from InteractionLog
      - If no records exist, since=None (fetch everything)
    - Step 3: Fetch logs since that timestamp and load them
      - Pass the raw items list to load_logs so it can map short IDs
        to titles
    - Return a dict: {"new_records": <number of new interactions>,
                      "total_records": <total interactions in DB>}
    """
    from sqlmodel import col, desc, select

    from app.models.interaction import InteractionLog

    items = await fetch_items()
    await load_items(items, session)

    result = await session.exec(
        select(InteractionLog).order_by(desc(InteractionLog.created_at)).limit(1)
    )
    last_log = result.first()
    since = last_log.created_at if last_log else None

    logs = await fetch_logs(since=since)
    new_records = await load_logs(logs, items, session)

    result = await session.exec(select(InteractionLog))
    total_records = len(list(result.all()))

    return {"new_records": new_records, "total_records": total_records}
