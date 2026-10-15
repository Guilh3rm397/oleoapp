from datetime import datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase

from app.db.collections import DISPOSALS, POINTS


async def find_active_point(db: AsyncDatabase, point_oid: ObjectId) -> dict | None:
    return await db[POINTS].find_one({"_id": point_oid, "active": True}, {"name": 1})


async def insert(db: AsyncDatabase, doc: dict) -> ObjectId:
    result = await db[DISPOSALS].insert_one(doc)
    return result.inserted_id


async def list_by_user(db: AsyncDatabase, user_oid: ObjectId, limit: int) -> list[dict]:
    cursor = (
        db[DISPOSALS]
        .find({"user_id": user_oid})
        .sort([("created_at", -1), ("_id", -1)])
        .limit(limit)
    )
    return await cursor.to_list(length=limit)


async def delete_one(db: AsyncDatabase, user_oid: ObjectId, disposal_oid: ObjectId) -> bool:
    result = await db[DISPOSALS].delete_one({"_id": disposal_oid, "user_id": user_oid})
    return result.deleted_count == 1


async def sum_volume(
    db: AsyncDatabase, user_oid: ObjectId, since: datetime | None
) -> tuple[int, int]:
    """Devolve (total_ml, quantidade de registros) do usuário, a partir de `since`."""
    match: dict = {"user_id": user_oid}
    if since is not None:
        match["created_at"] = {"$gte": since}
    pipeline = [
        {"$match": match},
        {"$group": {"_id": None, "total_ml": {"$sum": "$volume_ml"}, "count": {"$sum": 1}}},
    ]
    cursor = await db[DISPOSALS].aggregate(pipeline)
    docs = await cursor.to_list(length=1)
    if not docs:
        return 0, 0
    return int(docs[0]["total_ml"]), int(docs[0]["count"])


async def find_last(db: AsyncDatabase, user_oid: ObjectId) -> dict | None:
    return await db[DISPOSALS].find_one(
        {"user_id": user_oid}, {"created_at": 1}, sort=[("created_at", -1)]
    )
