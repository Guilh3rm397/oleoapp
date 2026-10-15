from typing import Annotated

from fastapi import APIRouter, Depends
from pymongo.asynchronous.database import AsyncDatabase

from app.api.deps import CurrentUser, get_current_user, get_db
from app.schemas.favorites import FavoriteListOut
from app.services import favorites as favorites_service

router = APIRouter(prefix="/favorites", tags=["favorites"])

UserDep = Annotated[CurrentUser, Depends(get_current_user)]
DbDep = Annotated[AsyncDatabase, Depends(get_db)]


@router.get("", response_model=FavoriteListOut)
async def list_favorites(user: UserDep, db: DbDep) -> FavoriteListOut:
    return FavoriteListOut(items=await favorites_service.list_favorites(db, user))


@router.put("/{point_id}", status_code=204)
async def add_favorite(point_id: str, user: UserDep, db: DbDep) -> None:
    await favorites_service.add_favorite(db, user, point_id)


@router.delete("/{point_id}", status_code=204)
async def remove_favorite(point_id: str, user: UserDep, db: DbDep) -> None:
    await favorites_service.remove_favorite(db, user, point_id)
