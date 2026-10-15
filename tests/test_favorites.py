from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.api.deps import CurrentUser, get_current_user
from app.db.collections import FAVORITES, POINTS


@pytest.fixture
def login(app):
    def _login(user_id: str | None = None) -> CurrentUser:
        current = CurrentUser(
            id=user_id or str(ObjectId()),
            name="Teste",
            email="teste@exemplo.com",
            reminder_interval_days=180,
        )
        app.dependency_overrides[get_current_user] = lambda: current
        return current

    yield _login
    app.dependency_overrides.pop(get_current_user, None)


async def insert_point(app, *, active: bool = True, name: str = "Ponto Teste") -> ObjectId:
    now = datetime.now(UTC)
    oid = ObjectId()
    await app.state.db[POINTS].insert_one(
        {
            "_id": oid,
            "slug": f"ponto-{oid}",
            "name": name,
            "address": {
                "street": "Rua Exemplo",
                "number": "100",
                "bairro": "Centro",
                "city": "Maricá",
                "state": "RJ",
                "cep": "24900-000",
            },
            "location": {"type": "Point", "coordinates": [-42.81, -22.91]},
            "phone": None,
            "opening_hours": None,
            "accepted_oils": ["motor_carro", "motor_moto"],
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


async def insert_favorite(app, user_id: str, point_id: ObjectId, created_at: datetime) -> None:
    await app.state.db[FAVORITES].insert_one(
        {"user_id": ObjectId(user_id), "point_id": point_id, "created_at": created_at}
    )


async def test_add_returns_204_and_lists(client, app, login):
    login()
    point_id = await insert_point(app, name="Ponto do Centro")

    put = await client.put(f"/api/favorites/{point_id}")
    listing = await client.get("/api/favorites")

    assert put.status_code == 204
    assert put.content == b""
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == str(point_id)
    assert item["name"] == "Ponto do Centro"
    assert item["bairro"] == "Centro"
    assert item["address_formatted"] == "Rua Exemplo, 100 - Centro, Maricá - RJ, 24900-000"
    assert item["location"] == {"lat": -22.91, "lng": -42.81}
    assert item["accepted_oils"] == ["motor_carro", "motor_moto"]
    assert item["verified"] is True
    assert item["favorited_at"].endswith("Z")
    assert "distance_m" not in item


async def test_add_twice_does_not_duplicate(client, app, login):
    login()
    point_id = await insert_point(app)

    first = await client.put(f"/api/favorites/{point_id}")
    first_item = (await client.get("/api/favorites")).json()["items"][0]
    second = await client.put(f"/api/favorites/{point_id}")
    items = (await client.get("/api/favorites")).json()["items"]

    assert first.status_code == 204
    assert second.status_code == 204
    assert await app.state.db[FAVORITES].count_documents({}) == 1
    assert len(items) == 1
    # A segunda chamada não muda a data original do favorito.
    assert items[0]["favorited_at"] == first_item["favorited_at"]


async def test_remove(client, app, login):
    login()
    point_id = await insert_point(app)
    await client.put(f"/api/favorites/{point_id}")

    resp = await client.delete(f"/api/favorites/{point_id}")

    assert resp.status_code == 204
    assert (await client.get("/api/favorites")).json() == {"items": []}


@pytest.mark.parametrize("point_id", [str(ObjectId()), "id-invalido"])
async def test_remove_nonexistent_is_204(client, login, point_id):
    login()

    resp = await client.delete(f"/api/favorites/{point_id}")

    assert resp.status_code == 204


async def test_remove_does_not_touch_other_users(client, app, login):
    other = str(ObjectId())
    point_id = await insert_point(app)
    await insert_favorite(app, other, point_id, datetime.now(UTC))
    login()

    resp = await client.delete(f"/api/favorites/{point_id}")

    assert resp.status_code == 204
    assert await app.state.db[FAVORITES].count_documents({}) == 1


@pytest.mark.parametrize("kind", ["missing", "invalid", "inactive"])
async def test_add_point_not_found(client, app, login, kind):
    login()
    if kind == "missing":
        point_id = str(ObjectId())
    elif kind == "invalid":
        point_id = "id-invalido"
    else:
        point_id = str(await insert_point(app, active=False))

    resp = await client.put(f"/api/favorites/{point_id}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert await app.state.db[FAVORITES].count_documents({}) == 0


async def test_list_hides_inactive_points(client, app, login):
    me = login()
    now = datetime.now(UTC)
    active_id = await insert_point(app, name="Ativo")
    inactive_id = await insert_point(app, name="Inativo")
    await insert_favorite(app, me.id, active_id, now - timedelta(hours=1))
    await insert_favorite(app, me.id, inactive_id, now)
    await app.state.db[POINTS].update_one({"_id": inactive_id}, {"$set": {"active": False}})

    resp = await client.get("/api/favorites")

    assert [item["name"] for item in resp.json()["items"]] == ["Ativo"]


async def test_list_hides_other_users_favorites(client, app, login):
    me = login()
    point_id = await insert_point(app, name="Meu")
    other_point_id = await insert_point(app, name="Do outro")
    await insert_favorite(app, me.id, point_id, datetime.now(UTC))
    await insert_favorite(app, str(ObjectId()), other_point_id, datetime.now(UTC))

    resp = await client.get("/api/favorites")

    assert [item["name"] for item in resp.json()["items"]] == ["Meu"]


async def test_list_ordered_by_favorited_at_desc(client, app, login):
    me = login()
    now = datetime.now(UTC)
    oldest = await insert_point(app, name="Mais antigo")
    middle = await insert_point(app, name="Do meio")
    newest = await insert_point(app, name="Mais novo")
    await insert_favorite(app, me.id, middle, now - timedelta(days=2))
    await insert_favorite(app, me.id, newest, now - timedelta(days=1))
    await insert_favorite(app, me.id, oldest, now - timedelta(days=3))

    resp = await client.get("/api/favorites")

    assert [item["name"] for item in resp.json()["items"]] == [
        "Mais novo",
        "Do meio",
        "Mais antigo",
    ]


async def test_list_empty(client, login):
    login()

    resp = await client.get("/api/favorites")

    assert resp.status_code == 200
    assert resp.json() == {"items": []}


async def test_requires_login(client):
    resp = await client.get("/api/favorites")

    assert resp.status_code == 401
