from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pymongo.asynchronous.database import AsyncDatabase

from app.api.deps import CurrentUser, get_current_user, get_db
from app.schemas.disposals import (
    DisposalIn,
    DisposalListOut,
    DisposalOut,
    ImpactOut,
    Period,
    ReminderOut,
)
from app.services import disposals as disposals_service

router = APIRouter(prefix="/disposals", tags=["disposals"])

UserDep = Annotated[CurrentUser, Depends(get_current_user)]
DbDep = Annotated[AsyncDatabase, Depends(get_db)]


@router.post("", response_model=DisposalOut, status_code=201)
async def create_disposal(body: DisposalIn, user: UserDep, db: DbDep) -> DisposalOut:
    return await disposals_service.create_disposal(db, user, body)


@router.get("", response_model=DisposalListOut)
async def list_disposals(
    user: UserDep, db: DbDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> DisposalListOut:
    return DisposalListOut(items=await disposals_service.list_disposals(db, user, limit))


# "/impact" e "/reminder" ficam antes de "/{disposal_id}".
@router.get("/impact", response_model=ImpactOut)
async def get_impact(user: UserDep, db: DbDep, period: Period = "30d") -> ImpactOut:
    return await disposals_service.get_impact(db, user, period)


@router.get("/reminder", response_model=ReminderOut)
async def get_reminder(user: UserDep, db: DbDep) -> ReminderOut:
    return await disposals_service.get_reminder(db, user)


@router.delete("/{disposal_id}", status_code=204)
async def delete_disposal(disposal_id: str, user: UserDep, db: DbDep) -> None:
    await disposals_service.delete_disposal(db, user, disposal_id)
