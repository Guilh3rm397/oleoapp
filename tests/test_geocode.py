import httpx
import pytest
from bson import ObjectId

from app.api.deps import CurrentUser, get_current_user, get_http_client
from app.core.config import settings

MARICA = (-22.9189, -42.8186)
CABO_FRIO = (-22.879, -42.019)  # fora do raio de 40 km


@pytest.fixture
def login(app):
    current = CurrentUser(
        id=str(ObjectId()), name="Teste", email="teste@exemplo.com", reminder_interval_days=180
    )
    app.dependency_overrides[get_current_user] = lambda: current
    yield current
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
def google(monkeypatch):
    monkeypatch.setattr(settings, "GEOCODER", "google")
    monkeypatch.setattr(settings, "GOOGLE_MAPS_API_KEY", "chave-teste")


@pytest.fixture
def nominatim(monkeypatch):
    monkeypatch.setattr(settings, "GEOCODER", "nominatim")
    monkeypatch.setattr(settings, "NOMINATIM_USER_AGENT", "OleoApp-Teste/1.0")


def google_payload(*places: tuple[str, float, float]) -> dict:
    return {
        "status": "OK",
        "results": [
            {"formatted_address": label, "geometry": {"location": {"lat": lat, "lng": lng}}}
            for label, lat, lng in places
        ],
    }


def nominatim_payload(*places: tuple[str, float, float]) -> list[dict]:
    # O Nominatim devolve lat/lon como texto.
    return [
        {"display_name": label, "lat": str(lat), "lon": str(lng)} for label, lat, lng in places
    ]


def recorder(responses: dict[str, httpx.Response]):
    """Cria um handler que responde por host e guarda as chamadas."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host not in responses:
            raise AssertionError(f"Chamada inesperada para {request.url.host}")
        return responses[request.url.host]

    return handler, calls


# ---------- validação e login ----------


async def test_requires_login(client):
    resp = await client.get("/api/geocode", params={"q": "centro"})

    assert resp.status_code == 401


async def test_query_too_short(client, login, mock_http, google):
    handler, calls = recorder({})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "ab"})

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert calls == []


async def test_query_too_long(client, login, mock_http, google):
    handler, _ = recorder({})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "a" * 121})

    assert resp.status_code == 422


# ---------- CEP ----------


@pytest.mark.parametrize("cep", ["24900-000", "24900000"])
async def test_cep_from_marica(client, login, mock_http, google, cep):
    viacep = httpx.Response(
        200,
        json={
            "cep": "24900-000",
            "logradouro": "Rua Exemplo",
            "bairro": "Centro",
            "localidade": "Maricá",
            "uf": "RJ",
        },
    )
    geocoded = httpx.Response(200, json=google_payload(("Rua Exemplo, Maricá - RJ", *MARICA)))
    handler, calls = recorder({"viacep.com.br": viacep, "maps.googleapis.com": geocoded})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": cep})

    assert resp.status_code == 200
    assert resp.json() == {
        "items": [{"label": "Rua Exemplo, Maricá - RJ", "lat": MARICA[0], "lng": MARICA[1]}]
    }
    assert str(calls[0].url) == "https://viacep.com.br/ws/24900000/json/"
    assert calls[1].url.params["address"] == "Rua Exemplo, Centro, Maricá - RJ"


async def test_cep_city_comparison_ignores_accents_and_case(client, login, mock_http, google):
    viacep = httpx.Response(
        200, json={"logradouro": "", "bairro": "Centro", "localidade": "MARICA", "uf": "RJ"}
    )
    geocoded = httpx.Response(200, json=google_payload(("Centro, Maricá - RJ", *MARICA)))
    handler, calls = recorder({"viacep.com.br": viacep, "maps.googleapis.com": geocoded})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "24900-000"})

    assert len(resp.json()["items"]) == 1
    # Partes vazias são omitidas.
    assert calls[1].url.params["address"] == "Centro, Maricá - RJ"


async def test_cep_from_other_city_returns_empty(client, login, mock_http, google):
    viacep = httpx.Response(
        200,
        json={"logradouro": "Rua X", "bairro": "Icaraí", "localidade": "Niterói", "uf": "RJ"},
    )
    handler, calls = recorder({"viacep.com.br": viacep})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "24220-000"})

    assert resp.status_code == 200
    assert resp.json() == {"items": []}
    assert [call.url.host for call in calls] == ["viacep.com.br"]  # o geocodificador não é chamado


@pytest.mark.parametrize("erro", [True, "true"])
async def test_cep_not_found_returns_empty(client, login, mock_http, google, erro):
    handler, calls = recorder({"viacep.com.br": httpx.Response(200, json={"erro": erro})})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "00000-000"})

    assert resp.status_code == 200
    assert resp.json() == {"items": []}
    assert [call.url.host for call in calls] == ["viacep.com.br"]


async def test_viacep_failure_becomes_502(client, login, mock_http, google):
    handler, _ = recorder({"viacep.com.br": httpx.Response(500)})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "24900-000"})

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"


# ---------- texto livre ----------


async def test_free_text_google(client, login, mock_http, google):
    geocoded = httpx.Response(200, json=google_payload(("Centro, Maricá - RJ", *MARICA)))
    handler, calls = recorder({"maps.googleapis.com": geocoded})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "  Centro   Maricá "})

    assert resp.status_code == 200
    assert resp.json() == {
        "items": [{"label": "Centro, Maricá - RJ", "lat": MARICA[0], "lng": MARICA[1]}]
    }
    assert len(calls) == 1
    params = calls[0].url.params
    assert str(calls[0].url).startswith("https://maps.googleapis.com/maps/api/geocode/json")
    assert params["address"] == "Centro Maricá"  # espaços normalizados
    assert params["key"] == "chave-teste"
    assert params["language"] == "pt-BR"
    assert params["region"] == "br"
    assert params["components"] == "country:BR|administrative_area:RJ"


async def test_free_text_nominatim(client, login, mock_http, nominatim):
    geocoded = httpx.Response(200, json=nominatim_payload(("Itaipuaçu, Maricá", -22.96, -42.93)))
    handler, calls = recorder({"nominatim.openstreetmap.org": geocoded})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "Itaipuaçu"})

    assert resp.status_code == 200
    assert resp.json() == {"items": [{"label": "Itaipuaçu, Maricá", "lat": -22.96, "lng": -42.93}]}
    request = calls[0]
    params = request.url.params
    assert str(request.url).startswith("https://nominatim.openstreetmap.org/search")
    assert params["q"] == "Itaipuaçu"
    assert params["format"] == "jsonv2"
    assert params["countrycodes"] == "br"
    assert params["limit"] == "5"
    assert params["bounded"] == "1"
    assert request.headers["User-Agent"] == "OleoApp-Teste/1.0"

    lng_min, lat_max, lng_max, lat_min = (float(v) for v in params["viewbox"].split(","))
    assert lng_min < MARICA[1] < lng_max
    assert lat_min < MARICA[0] < lat_max


async def test_nominatim_does_not_need_google_key(client, login, mock_http, nominatim, monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_MAPS_API_KEY", "")
    geocoded = httpx.Response(200, json=nominatim_payload(("Centro, Maricá", *MARICA)))
    handler, _ = recorder({"nominatim.openstreetmap.org": geocoded})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "centro"})

    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


async def test_cep_with_nominatim(client, login, mock_http, nominatim):
    viacep = httpx.Response(
        200, json={"logradouro": "Rua Exemplo", "bairro": "Centro", "localidade": "Maricá"}
    )
    geocoded = httpx.Response(200, json=nominatim_payload(("Rua Exemplo, Maricá", *MARICA)))
    handler, calls = recorder({"viacep.com.br": viacep, "nominatim.openstreetmap.org": geocoded})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "24900-000"})

    assert len(resp.json()["items"]) == 1
    assert calls[1].url.params["q"] == "Rua Exemplo, Centro, Maricá - RJ"


# ---------- filtros ----------


async def test_results_outside_area_are_dropped(client, login, mock_http, google):
    geocoded = httpx.Response(
        200,
        json=google_payload(("Cabo Frio - RJ", *CABO_FRIO), ("Centro, Maricá - RJ", *MARICA)),
    )
    handler, _ = recorder({"maps.googleapis.com": geocoded})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "centro"})

    assert [item["label"] for item in resp.json()["items"]] == ["Centro, Maricá - RJ"]


async def test_at_most_five_results(client, login, mock_http, google):
    places = [(f"Rua {n}, Maricá - RJ", MARICA[0], MARICA[1]) for n in range(7)]
    geocoded = httpx.Response(200, json=google_payload(*places))
    handler, _ = recorder({"maps.googleapis.com": geocoded})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "rua"})

    assert len(resp.json()["items"]) == 5


async def test_zero_results_returns_empty(client, login, mock_http, google):
    handler, _ = recorder(
        {"maps.googleapis.com": httpx.Response(200, json={"status": "ZERO_RESULTS", "results": []})}
    )
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "lugar nenhum"})

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ---------- erros ----------


async def test_google_without_key_becomes_503(client, login, mock_http, monkeypatch):
    monkeypatch.setattr(settings, "GEOCODER", "google")
    monkeypatch.setattr(settings, "GOOGLE_MAPS_API_KEY", "")
    handler, calls = recorder({})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "centro"})

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "service_unavailable"
    assert calls == []


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, json={"status": "REQUEST_DENIED", "results": []}),
        httpx.Response(200, content=b"isto nao e json"),
    ],
)
async def test_provider_failure_becomes_502(client, login, mock_http, google, response):
    handler, _ = recorder({"maps.googleapis.com": response})
    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "centro"})

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"


async def test_network_error_becomes_502(client, login, mock_http, google):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rede", request=request)

    mock_http(handler)

    resp = await client.get("/api/geocode", params={"q": "centro"})

    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "upstream_error"


# ---------- cache ----------


async def test_cache_avoids_second_provider_call(client, app, login, mock_http, google):
    if app.state.redis is None:
        pytest.skip("O cache precisa do Redis")
    geocoded = httpx.Response(200, json=google_payload(("Centro, Maricá - RJ", *MARICA)))
    handler, calls = recorder({"maps.googleapis.com": geocoded})
    mock_http(handler)

    first = await client.get("/api/geocode", params={"q": "Centro Maricá"})
    # Maiúsculas e espaços diferentes caem na mesma chave de cache.
    second = await client.get("/api/geocode", params={"q": "  centro   MARICÁ "})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1


async def test_cache_also_covers_cep(client, app, login, mock_http, google):
    if app.state.redis is None:
        pytest.skip("O cache precisa do Redis")
    viacep = httpx.Response(
        200, json={"logradouro": "Rua Exemplo", "bairro": "Centro", "localidade": "Maricá"}
    )
    geocoded = httpx.Response(200, json=google_payload(("Rua Exemplo, Maricá - RJ", *MARICA)))
    handler, calls = recorder({"viacep.com.br": viacep, "maps.googleapis.com": geocoded})
    mock_http(handler)

    await client.get("/api/geocode", params={"q": "24900-000"})
    resp = await client.get("/api/geocode", params={"q": "24900-000"})

    assert len(resp.json()["items"]) == 1
    assert len(calls) == 2  # ViaCEP + Google só na primeira vez


async def test_failures_are_not_cached(client, app, login, mock_http, google):
    if app.state.redis is None:
        pytest.skip("O cache precisa do Redis")
    responses = iter(
        [
            httpx.Response(500),
            httpx.Response(200, json=google_payload(("Centro, Maricá - RJ", *MARICA))),
        ]
    )
    mock_http(lambda request: next(responses))

    first = await client.get("/api/geocode", params={"q": "centro"})
    second = await client.get("/api/geocode", params={"q": "centro"})

    assert first.status_code == 502
    assert second.status_code == 200
    assert len(second.json()["items"]) == 1
