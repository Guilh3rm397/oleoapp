from datetime import UTC, datetime, timedelta

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from app.api.deps import CurrentUser
from app.core.errors import AppError
from app.core.impact_factors import IMPACT_FACTORS, IMPACT_SOURCES
from app.repositories import disposals as repo
from app.schemas.disposals import (
    DisposalIn,
    DisposalOut,
    DisposalPointOut,
    ImpactFactorsOut,
    ImpactOut,
    ImpactSourcesOut,
    Period,
    ReminderOut,
    ReminderStatus,
)

PERIOD_DAYS: dict[str, int | None] = {"30d": 30, "6m": 182, "12m": 365, "all": None}
SOON_THRESHOLD = 0.8


def _not_found(message: str) -> AppError:
    return AppError(404, "not_found", message)


def _to_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _to_out(doc: dict) -> DisposalOut:
    point = None
    if doc.get("point_id") is not None:
        point = DisposalPointOut(id=str(doc["point_id"]), name=doc.get("point_name") or "")
    return DisposalOut(
        id=str(doc["_id"]),
        volume_ml=doc["volume_ml"],
        point=point,
        created_at=doc["created_at"],
    )


async def create_disposal(db: AsyncDatabase, user: CurrentUser, data: DisposalIn) -> DisposalOut:
    point_oid: ObjectId | None = None
    point_name: str | None = None

    if data.point_id is not None:
        point_oid = _to_object_id(data.point_id)
        point = await repo.find_active_point(db, point_oid) if point_oid else None
        if point is None:
            raise _not_found("Ponto de coleta não encontrado.")
        point_name = point["name"]

    doc = {
        "user_id": ObjectId(user.id),
        "point_id": point_oid,
        "point_name": point_name,
        "volume_ml": data.volume_ml,
        "created_at": datetime.now(UTC),
    }
    doc["_id"] = await repo.insert(db, doc)
    return _to_out(doc)


async def list_disposals(db: AsyncDatabase, user: CurrentUser, limit: int) -> list[DisposalOut]:
    docs = await repo.list_by_user(db, ObjectId(user.id), limit)
    return [_to_out(doc) for doc in docs]


async def delete_disposal(db: AsyncDatabase, user: CurrentUser, disposal_id: str) -> None:
    disposal_oid = _to_object_id(disposal_id)
    deleted = False
    if disposal_oid is not None:
        deleted = await repo.delete_one(db, ObjectId(user.id), disposal_oid)
    if not deleted:
        raise _not_found("Descarte não encontrado.")


async def get_impact(db: AsyncDatabase, user: CurrentUser, period: Period) -> ImpactOut:
    days = PERIOD_DAYS[period]
    since = datetime.now(UTC) - timedelta(days=days) if days is not None else None
    total_ml, count = await repo.sum_volume(db, ObjectId(user.id), since)

    liters = total_ml / 1000
    water_l = round(liters * IMPACT_FACTORS["water_l_per_l_oluc"])
    co2e_kg = round(liters * IMPACT_FACTORS["co2e_kg_per_l_oluc"], 1)

    return ImpactOut(
        period=period,
        total_ml=total_ml,
        count=count,
        water_l=water_l,
        co2e_kg=co2e_kg,
        factors_version=IMPACT_FACTORS["version"],
        factors=ImpactFactorsOut(
            water_l_per_l_oluc=IMPACT_FACTORS["water_l_per_l_oluc"],
            co2e_kg_per_l_oluc=IMPACT_FACTORS["co2e_kg_per_l_oluc"],
        ),
        sources=ImpactSourcesOut(**IMPACT_SOURCES),
    )


def _reminder_status(days_since: int, interval: int) -> ReminderStatus:
    if days_since >= interval:
        return "overdue"
    if days_since >= SOON_THRESHOLD * interval:
        return "soon"
    return "ok"


async def get_reminder(db: AsyncDatabase, user: CurrentUser) -> ReminderOut:
    interval = user.reminder_interval_days
    last = await repo.find_last(db, ObjectId(user.id))
    if last is None:
        return ReminderOut(status="none", reminder_interval_days=interval)

    last_at: datetime = last["created_at"]
    days_since = max(0, (datetime.now(UTC) - last_at).days)
    return ReminderOut(
        status=_reminder_status(days_since, interval),
        last_disposal_at=last_at,
        days_since=days_since,
        days_left=interval - days_since,
        reminder_interval_days=interval,
    )
