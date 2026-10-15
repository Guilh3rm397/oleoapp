from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Query
from redis.asyncio import Redis

from app.api.deps import CurrentUser, get_current_user, get_http_client, get_redis, rate_limit
from app.schemas.geocode import GeocodeOut
from app.services import geocode as geocode_service

router = APIRouter(prefix="/geocode", tags=["geocode"])


@router.get(
    "",
    response_model=GeocodeOut,
    dependencies=[Depends(rate_limit("geocode", "RATE_LIMIT_GEOCODE_PER_MIN", 60, by="user"))],
)
async def geocode(
    q: Annotated[str, Query(min_length=3, max_length=120)],
    _user: Annotated[CurrentUser, Depends(get_current_user)],
    redis: Annotated[Redis | None, Depends(get_redis)],
    http: Annotated[httpx.AsyncClient, Depends(get_http_client)],
) -> GeocodeOut:
    return GeocodeOut(items=await geocode_service.search_address(q, redis, http))
