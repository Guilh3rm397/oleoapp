from pydantic import BaseModel, ConfigDict

from app.schemas.common import LatLng


class DirectionsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: LatLng
    point_id: str


class DirectionsOut(BaseModel):
    encoded_polyline: str
    distance_m: int | None = None
    duration_s: int | None = None
