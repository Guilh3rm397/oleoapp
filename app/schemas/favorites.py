from datetime import datetime

from pydantic import BaseModel

from app.schemas.common import LatLng


class FavoritePointOut(BaseModel):
    id: str
    name: str
    bairro: str
    address_formatted: str
    location: LatLng
    accepted_oils: list[str]
    verified: bool
    favorited_at: datetime


class FavoriteListOut(BaseModel):
    items: list[FavoritePointOut]
