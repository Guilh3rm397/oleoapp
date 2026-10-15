from typing import Annotated

import httpx
from fastapi import APIRouter, Depends
from pymongo.asynchronous.database import AsyncDatabase

from app.api.deps import CurrentUser, get_current_user, get_db, get_http_client, rate_limit
from app.schemas.directions import DirectionsIn, DirectionsOut
from app.services import directions as directions_service

router = APIRouter(prefix="/directions", tags=["directions"])


@router.post(
    "",
    response_model=DirectionsOut,
    dependencies=[
        Depends(rate_limit("directions", "RATE_LIMIT_DIRECTIONS_PER_HOUR", 3600, by="user"))
    ],
)
async def create_directions(
    body: DirectionsIn,
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncDatabase, Depends(get_db)],
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> DirectionsOut:
    return await directions_service.get_route(db, http, body.origin, body.point_id)
