from datetime import UTC, datetime

from bson import ObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from app.api.deps import CurrentUser
from app.core.errors import AppError
from app.repositories import favorites as repo
from app.schemas.common import LatLng
from app.schemas.favorites import FavoritePointOut
from app.schemas.points import format_address  # exportada pela BE-04 (não duplicar a regra)


def _to_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _to_out(point: dict, favorited_at: datetime) -> FavoritePointOut:
    lng, lat = point["location"]["coordinates"]
    address = point["address"]
    return FavoritePointOut(
        id=str(point["_id"]),
        name=point["name"],
        bairro=address["bairro"],
        address_formatted=format_address(address),
        location=LatLng(lat=lat, lng=lng),
        accepted_oils=list(point["accepted_oils"]),
        verified=point["verified"],
        favorited_at=favorited_at,
    )


async def list_favorites(db: AsyncDatabase, user: CurrentUser) -> list[FavoritePointOut]:
    favorites = await repo.list_by_user(db, ObjectId(user.id))
    if not favorites:
        return []

    points = await repo.find_active_points(db, [fav["point_id"] for fav in favorites])
    points_by_id = {point["_id"]: point for point in points}

    # Mantém a ordem dos favoritos (mais recente primeiro) e pula os pontos inativos.
    return [
        _to_out(points_by_id[fav["point_id"]], fav["created_at"])
        for fav in favorites
        if fav["point_id"] in points_by_id
    ]


async def add_favorite(db: AsyncDatabase, user: CurrentUser, point_id: str) -> None:
    point_oid = _to_object_id(point_id)
    point = await repo.find_active_point(db, point_oid) if point_oid else None
    if point is None:
        raise AppError(404, "not_found", "Ponto de coleta não encontrado.")
    await repo.upsert(db, ObjectId(user.id), point_oid, datetime.now(UTC))


async def remove_favorite(db: AsyncDatabase, user: CurrentUser, point_id: str) -> None:
    point_oid = _to_object_id(point_id)
    if point_oid is None:
        return  # idempotente: id inválido também responde 204
    await repo.delete(db, ObjectId(user.id), point_oid)
