import logging

import httpx
from bson import ObjectId
from bson.errors import InvalidId
from pymongo.asynchronous.database import AsyncDatabase

from app.core.config import settings
from app.core.errors import AppError
from app.core.geo import within_area
from app.db.collections import POINTS
from app.schemas.common import LatLng
from app.schemas.directions import DirectionsOut

logger = logging.getLogger(__name__)

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline"


def _upstream_error() -> AppError:
    return AppError(502, "upstream_error", "Não foi possível calcular a rota agora.")


def _point_not_found() -> AppError:
    return AppError(404, "not_found", "Ponto de coleta não encontrado.")


def _parse_duration(value: object) -> int | None:
    # O Google devolve a duração como texto, por exemplo "123s".
    if isinstance(value, str) and value.endswith("s"):
        try:
            return round(float(value[:-1]))
        except ValueError:
            return None
    return None


def _parse_distance(value: object) -> int | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return int(value)
    return None


async def _get_destination(db: AsyncDatabase, point_id: str) -> tuple[float, float]:
    """Devolve (lat, lng) do ponto ativo. O destino vem sempre do banco."""
    try:
        oid = ObjectId(point_id)
    except (InvalidId, TypeError):
        raise _point_not_found() from None
    doc = await db[POINTS].find_one({"_id": oid, "active": True}, {"location": 1})
    if doc is None:
        raise _point_not_found()
    lng, lat = doc["location"]["coordinates"]
    return lat, lng


async def get_route(
    db: AsyncDatabase, http: httpx.AsyncClient, origin: LatLng, point_id: str
) -> DirectionsOut:
    if not settings.GOOGLE_MAPS_API_KEY:
        raise AppError(503, "service_unavailable", "Rotas indisponíveis no momento.")

    if not within_area(origin.lat, origin.lng):
        raise AppError(400, "out_of_area", "A rota no app só funciona em Maricá e arredores.")

    dest_lat, dest_lng = await _get_destination(db, point_id)

    body = {
        "origin": {"location": {"latLng": {"latitude": origin.lat, "longitude": origin.lng}}},
        "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lng}}},
        "travelMode": "DRIVE",
        "languageCode": "pt-BR",
        "units": "METRIC",
    }
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.GOOGLE_MAPS_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }

    try:
        response = await http.post(ROUTES_URL, json=body, headers=headers)
    except httpx.HTTPError:
        # Sem coordenadas e sem a mensagem do erro no log.
        logger.warning("Routes API: erro de rede")
        raise _upstream_error() from None

    if response.status_code != 200:
        logger.warning("Routes API respondeu com status %s", response.status_code)
        raise _upstream_error()

    try:
        route = response.json()["routes"][0]
        polyline = route["polyline"]["encodedPolyline"]
    except (ValueError, KeyError, IndexError, TypeError):
        logger.warning("Routes API respondeu sem polilinha")
        raise _upstream_error() from None

    if not isinstance(polyline, str) or not polyline:
        logger.warning("Routes API respondeu sem polilinha")
        raise _upstream_error()

    return DirectionsOut(
        encoded_polyline=polyline,
        distance_m=_parse_distance(route.get("distanceMeters")),
        duration_s=_parse_duration(route.get("duration")),
    )
