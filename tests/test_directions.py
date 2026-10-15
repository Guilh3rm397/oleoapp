import json
from datetime import UTC, datetime

import httpx
import pytest
from bson import ObjectId

from app.api.deps import CurrentUser, get_current_user, get_http_client
from app.core.config import settings
from app.db.collections import POINTS

ORIGIN = {"lat": -22.9189, "lng": -42.8186}
FAR_ORIGIN = {"lat": -23.5505, "lng": -46.6333}  # São Paulo
POINT_LAT, POINT_LNG = -22.93, -42.80
FIELD_MASK = "routes.distanceMeters,routes.duration,routes.polyline.encodedPolyline"
GOOGLE_OK = {
    "routes": [
        {
            "distanceMeters": 5400,
            "duration": "780s",
            "polyline": {"encodedPolyline": "abc123"},
        }
    ]
}


@pytest.fixture
def login(app):
    def _login() -> CurrentUser:
        current = CurrentUser(
            id=str(ObjectId()),
            name="Teste",
            email="teste@exemplo.com",
            reminder_interval_days=180,
        )
        app.dependency_overrides[get_current_user] = lambda: current
        return current

    yield _login
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
async def mock_http(app):
    clients: list[httpx.AsyncClient] = []

    def _install(handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        clients.append(client)
        app.dependency_overrides[get_http_client] = lambda: client

    yield _install
    app.dependency_overrides.pop(get_http_client, None)
    for client in clients:
        await client.aclose()


@pytest.fixture
def google_key(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_MAPS_API_KEY", "chave-teste")


async def insert_point(app, *, active: bool = True) -> ObjectId:
    now = datetime.now(UTC)
    oid = ObjectId()
    await app.state.db[POINTS].insert_one(
        {
            "_id": oid,
            "slug": f"ponto-{oid}",
            "name": "Ponto Teste",
            "address": {
                "street": "Rua Exemplo",
                "number": "100",
                "bairro": "Centro",
                "city": "Maricá",
                "state": "RJ",
                "cep": "24900-000",
            },
            "location": {"type": "Point", "coordinates": [POINT_LNG, POINT_LAT]},
            "phone": None,
            "opening_hours": None,
            "accepted_oils": ["motor_carro"],
            "conditions": None,
            "verified": True,
            "verified_at": now,
            "source": "teste",
            "google_place_id": None,
            "active": active,
            "created_at": now,
            "updated_at": now,
        }
    )
    return oid


def never_called(request: httpx.Request) -> httpx.Response:
    raise AssertionError("O Google não deveria ter sido chamado")


async def test_requires_login(client):
    resp = await client.post("/api/directions", json={"origin": ORIGIN, "point_id": "x"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_success(client, app, login, mock_http, google_key):
    login()
    point_id = await insert_point(app)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=GOOGLE_OK)

    mock_http(handler)

    resp = await client.post(
        "/api/directions", json={"origin": ORIGIN, "point_id": str(point_id)}
    )

    assert resp.status_code == 200
    assert resp.json() == {"encoded_polyline": "abc123", "distance_m": 5400, "duration_s": 780}

    assert len(captured) == 1
    request = captured[0]
    assert str(request.url) == "https://routes.googleapis.com/directions/v2:computeRoutes"
    assert request.headers["X-Goog-Api-Key"] == "chave-teste"
    assert request.headers["X-Goog-FieldMask"] == FIELD_MASK
    body = json.loads(request.content)
    # O destino vem do banco, não do cliente.
    assert body["destination"]["location"]["latLng"] == {
        "latitude": POINT_LAT,
        "longitude": POINT_LNG,
    }
    assert body["origin"]["location"]["latLng"] == {
        "latitude": ORIGIN["lat"],
        "longitude": ORIGIN["lng"],
    }
    assert body["travelMode"] == "DRIVE"
    assert body["languageCode"] == "pt-BR"
    assert body["units"] == "METRIC"


async def test_distance_missing_becomes_null(client, app, login, mock_http, google_key):
    login()
    point_id = await insert_point(app)
    payload = {"routes": [{"polyline": {"encodedPolyline": "abc123"}}]}
    mock_http(lambda request: httpx.Response(200, json=payload))

    resp = await client.post(
        "/api/directions", json={"origin": ORIGIN, "point_id": str(point_id)}
    )

    assert resp.status_code == 200
    assert resp.json() == {"encoded_polyline": "abc123", "distance_m": None, "duration_s": None}


async def test_rejects_destination_from_client(client, app, login, mock_http, google_key):
    login()
    point_id = await insert_point(app)
    mock_http(never_called)

    resp = await client.post(
        "/api/directions",
        json={
            "origin": ORIGIN,
            "point_id": str(point_id),
            "destination": {"lat": -22.9, "lng": -42.8},
        },
    )

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


async def test_origin_out_of_area(client, app, login, mock_http, google_key):
    login()
    point_id = await insert_point(app)
    mock_http(never_called)

    resp = await client.post(
        "/api/directions", json={"origin": FAR_ORIGIN, "point_id": str(point_id)}
    )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "out_of_area"


@pytest.mark.parametrize("kind", ["missing", "invalid", "inactive"])
async def test_point_not_found(client, app, login, mock_http, google_key, kind):
    login()
    mock_http(never_called)
    if kind == "missing":
        point_id = str(ObjectId())
    elif kind == "invalid":
        point_id = "id-invalido"
    else:
        point_id = str(await insert_point(app, active=False))

    resp = await client.post("/api/directions", json={"origin": ORIGIN, "point_id": point_id})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


async def test_google_error_becomes_502(client, app, login, mock_http, google_key):
    login()
    point_id = await insert_point(app)
    mock_http(lambda request: httpx.Response(500, json={"error": {"message": "segredo interno"}}))

    resp = await client.post(
        "/api/directions", json={"origin": ORIGIN, "point_id": str(point_id)}
    )

    assert resp.status_code == 502
    body = resp.json()["error"]
    assert body["code"] == "upstream_error"
    assert "segredo interno" not in body["message"]


async def test_response_without_polyline_becomes_502(client, app, login, mock_http, google_key):
    login()
    point_id = await insert_point(app)
    mock_http(lambda request: httpx.Response(200, json={"routes": []}))

    resp = await client.post(
        "/api/directions", json={"origin": ORIGIN, "point_id": str(point_id)}
    )

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"


async def test_network_error_becomes_502(client, app, login, mock_http, google_key):
    login()
    point_id = await insert_point(app)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rede", request=request)

    mock_http(handler)

    resp = await client.post(
        "/api/directions", json={"origin": ORIGIN, "point_id": str(point_id)}
    )

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"


async def test_missing_api_key_becomes_503(client, app, login, mock_http, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_MAPS_API_KEY", "")
    login()
    point_id = await insert_point(app)
    mock_http(never_called)

    resp = await client.post(
        "/api/directions", json={"origin": ORIGIN, "point_id": str(point_id)}
    )

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "service_unavailable"


async def test_rate_limit(client, app, login, mock_http, google_key, monkeypatch):
    if app.state.redis is None:
        pytest.skip("O rate limit precisa do Redis")
    monkeypatch.setattr(settings, "RATE_LIMIT_DIRECTIONS_PER_HOUR", 1)
    login()
    point_id = await insert_point(app)
    mock_http(lambda request: httpx.Response(200, json=GOOGLE_OK))
    payload = {"origin": ORIGIN, "point_id": str(point_id)}

    first = await client.post("/api/directions", json=payload)
    second = await client.post("/api/directions", json=payload)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert "retry-after" in second.headers
