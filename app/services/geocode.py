import hashlib
import logging
import math
import re
import unicodedata

import httpx
from redis.asyncio import Redis

from app.core.cache import get_json, set_json
from app.core.config import settings
from app.core.errors import AppError
from app.core.geo import within_area
from app.schemas.geocode import GeocodeResult

logger = logging.getLogger(__name__)

VIACEP_URL = "https://viacep.com.br/ws/{cep}/json/"
GOOGLE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

CEP_RE = re.compile(r"\d{5}-?\d{3}")
CITY = "Maricá"
MAX_RESULTS = 5
MIN_QUERY_LEN = 3
CACHE_TTL_S = 7 * 24 * 3600


def _upstream_error() -> AppError:
    return AppError(502, "upstream_error", "Não foi possível buscar o endereço agora.")


def normalize_query(q: str) -> str:
    """Tira os espaços das pontas e junta espaços repetidos."""
    return " ".join(q.split())


def _fold(text: str) -> str:
    """Minúsculas e sem acentos, para comparar nomes de cidade."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def _area_viewbox() -> str:
    """Caixa da área atendida no formato do Nominatim: lng_min,lat_max,lng_max,lat_min."""
    lat, lng, radius = settings.AREA_CENTER_LAT, settings.AREA_CENTER_LNG, settings.AREA_RADIUS_KM
    d_lat = radius / 111.32
    d_lng = radius / (111.32 * math.cos(math.radians(lat)))
    return f"{lng - d_lng:.6f},{lat + d_lat:.6f},{lng + d_lng:.6f},{lat - d_lat:.6f}"


async def _fetch_json(
    http: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
):
    """GET que devolve o JSON. Qualquer falha vira 502. Não loga a busca nem coordenadas."""
    try:
        response = await http.get(url, params=params, headers=headers)
    except httpx.HTTPError:
        logger.warning("Geocodificação: erro de rede")
        raise _upstream_error() from None
    if response.status_code != 200:
        logger.warning("Geocodificação: provedor respondeu com status %s", response.status_code)
        raise _upstream_error()
    try:
        return response.json()
    except ValueError:
        logger.warning("Geocodificação: resposta inválida do provedor")
        raise _upstream_error() from None


async def _address_from_cep(digits: str, http: httpx.AsyncClient) -> str | None:
    """Consulta o ViaCEP. Devolve o texto para geocodificar, ou None se não for de Maricá."""
    data = await _fetch_json(http, VIACEP_URL.format(cep=digits))
    if not isinstance(data, dict) or data.get("erro") in (True, "true"):
        return None
    if _fold(str(data.get("localidade") or "")) != _fold(CITY):
        return None
    parts = [data.get("logradouro"), data.get("bairro"), f"{CITY} - RJ"]
    return ", ".join(part.strip() for part in parts if part and part.strip())


async def _geocode_google(text: str, http: httpx.AsyncClient) -> list[GeocodeResult]:
    params = {
        "address": text,
        "key": settings.GOOGLE_MAPS_API_KEY,
        "language": "pt-BR",
        "region": "br",
        "components": "country:BR|administrative_area:RJ",
    }
    data = await _fetch_json(http, GOOGLE_URL, params=params)
    status = data.get("status") if isinstance(data, dict) else None
    if status == "ZERO_RESULTS":
        return []
    if status != "OK":
        logger.warning("Geocoding API respondeu com status %s", status)
        raise _upstream_error()

    results = []
    for item in data.get("results", []):
        try:
            location = item["geometry"]["location"]
            results.append(
                GeocodeResult(
                    label=item["formatted_address"],
                    lat=float(location["lat"]),
                    lng=float(location["lng"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return results


async def _geocode_nominatim(text: str, http: httpx.AsyncClient) -> list[GeocodeResult]:
    params = {
        "q": text,
        "format": "jsonv2",
        "countrycodes": "br",
        "limit": MAX_RESULTS,
        "viewbox": _area_viewbox(),
        "bounded": 1,
    }
    headers = {"User-Agent": settings.NOMINATIM_USER_AGENT}
    data = await _fetch_json(http, NOMINATIM_URL, params=params, headers=headers)
    if not isinstance(data, list):
        raise _upstream_error()

    results = []
    for item in data:
        try:
            results.append(
                GeocodeResult(
                    label=item["display_name"],
                    lat=float(item["lat"]),
                    lng=float(item["lon"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return results


async def search_address(
    q: str, redis: Redis | None, http: httpx.AsyncClient
) -> list[GeocodeResult]:
    query = normalize_query(q)
    if len(query) < MIN_QUERY_LEN:
        raise AppError(422, "validation_error", "Dados inválidos.")

    cache_key = "geocode:v1:" + hashlib.sha1(query.lower().encode("utf-8")).hexdigest()
    cached = await get_json(redis, cache_key)
    if cached is not None:
        return [GeocodeResult(**item) for item in cached]

    if settings.GEOCODER == "google" and not settings.GOOGLE_MAPS_API_KEY:
        raise AppError(503, "service_unavailable", "Busca indisponível no momento.")

    if CEP_RE.fullmatch(query):
        text = await _address_from_cep(query.replace("-", ""), http)
    else:
        text = query

    results: list[GeocodeResult] = []
    if text is not None:
        if settings.GEOCODER == "google":
            found = await _geocode_google(text, http)
        else:
            found = await _geocode_nominatim(text, http)
        results = [r for r in found if within_area(r.lat, r.lng)][:MAX_RESULTS]

    await set_json(redis, cache_key, [r.model_dump() for r in results], CACHE_TTL_S)
    return results
