from datetime import datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from app.db.collections import FAVORITES, POINTS


async def find_active_point(db: AsyncDatabase, point_oid: ObjectId) -> dict | None:
    return await db[POINTS].find_one({"_id": point_oid, "active": True}, {"_id": 1})


async def upsert(
    db: AsyncDatabase, user_oid: ObjectId, point_oid: ObjectId, created_at: datetime
) -> None:
    try:
        await db[FAVORITES].update_one(
            {"user_id": user_oid, "point_id": point_oid},
            {"$setOnInsert": {"created_at": created_at}},
            upsert=True,
        )
    except DuplicateKeyError:
        # Duas requisições ao mesmo tempo: o favorito já existe, então está tudo certo.
        pass


async def delete(db: AsyncDatabase, user_oid: ObjectId, point_oid: ObjectId) -> None:
    await db[FAVORITES].delete_one({"user_id": user_oid, "point_id": point_oid})


async def list_by_user(db: AsyncDatabase, user_oid: ObjectId) -> list[dict]:
    cursor = db[FAVORITES].find({"user_id": user_oid}).sort([("created_at", -1), ("_id", -1)])
    return await cursor.to_list(length=None)


async def find_active_points(db: AsyncDatabase, point_oids: list[ObjectId]) -> list[dict]:
    cursor = db[POINTS].find({"_id": {"$in": point_oids}, "active": True})
    return await cursor.to_list(length=None)
