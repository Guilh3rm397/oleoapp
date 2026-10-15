from pydantic import BaseModel


class GeocodeResult(BaseModel):
    label: str
    lat: float
    lng: float


class GeocodeOut(BaseModel):
    items: list[GeocodeResult]
