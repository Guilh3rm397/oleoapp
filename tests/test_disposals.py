from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId

from app.api.deps import CurrentUser, get_current_user
from app.db.collections import DISPOSALS, POINTS


@pytest.fixture
def login(app):
    def _login(user_id: str | None = None, interval: int = 180) -> CurrentUser:
        current = CurrentUser(
            id=user_id or str(ObjectId()),
            name="Teste",
            email="teste@exemplo.com",
            reminder_interval_days=interval,
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
            "address": {"Rua": "Rua Exemplo", "bairro": "Centro", "cidade": "Maricá"},
            "location": {"tipo": "Point", "coordenadas": [-42.81, -22.91]},
            "accepted_oils": ["motor_carro"],
            "verified": True,
            "active": active,
            "created_at": now,
            "updated_at": now,
        }
    )
    return oid


async def insert_disposal(
    app, user_id: str, volume_ml: int, created_at: datetime | None = None
) -> ObjectId:
    oid = ObjectId()
    await app.state.db[DISPOSALS].insert_one(
        {
            "_id": oid,
            "user_id": ObjectId(user_id),
            "point_id": None,
            "point_name": None,
            "volume_ml": volume_ml,
            "created_at": created_at or datetime.now(UTC),
        }
    )
    return oid


def days_ago(days: int, *, minutes: int = 1) -> datetime:
    """Um pouco mais de dias atrás, para que days_since fique exatamente em `days`."""
    return datetime.now(UTC) - timedelta(days=days, minutes=minutes)


# Criação


async def test_create_without_point(client, login):
    login()

    resp = await client.post("/api/disposals", json={"volume_ml": 4000})

    assert resp.status_code == 201
    body = resp.json()
    assert body["volume_ml"] == 4000
    assert body["point"] is None
    assert body["id"]
    assert body["created_at"].endswith("Z")


async def test_create_with_point_copies_name(client, app, login):
    login()
    point_id = await insert_point(app, name="Ponto do Centro")

    resp = await client.post(
        "/api/disposals", json={"volume_ml": 1500, "point_id": str(point_id)}
    )

    assert resp.status_code == 201
    assert resp.json()["point"] == {"id": str(point_id), "name": "Ponto do Centro"}
    saved = await app.state.db[DISPOSALS].find_one({"_id": ObjectId(resp.json()["id"])})
    assert saved["point_name"] == "Ponto do Centro"
    assert saved["point_id"] == point_id


@pytest.mark.parametrize("volume", [0, 10001, -5])
async def test_create_invalid_volume(client, login, volume):
    login()

    resp = await client.post("/api/disposals", json={"volume_ml": volume})

    assert resp.status_code == 422
    error = resp.json()["error"]
    assert error["code"] == "validation_error"
    assert error["fields"][0]["field"] == "volume_ml"
    assert error["fields"][0]["message"] == "Informe de 1 mL a 10 L por registro."


@pytest.mark.parametrize("volume", [1, 10000])
async def test_create_volume_limits_are_accepted(client, login, volume):
    login()

    resp = await client.post("/api/disposals", json={"volume_ml": volume})

    assert resp.status_code == 201


async def test_create_rejects_unknown_field(client, login):
    login()

    resp = await client.post("/api/disposals", json={"volume_ml": 100, "extra": 1})

    assert resp.status_code == 422


@pytest.mark.parametrize("kind", ["missing", "invalid", "inactive"])
async def test_create_point_not_found(client, app, login, kind):
    login()
    if kind == "missing":
        point_id = str(ObjectId())
    elif kind == "invalid":
        point_id = "id-invalido"
    else:
        point_id = str(await insert_point(app, active=False))

    resp = await client.post("/api/disposals", json={"volume_ml": 100, "point_id": point_id})

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# lista


async def test_list_only_own_and_ordered(client, app, login):
    me = login()
    other = str(ObjectId())
    await insert_disposal(app, me.id, 100, days_ago(10))
    await insert_disposal(app, me.id, 300, days_ago(1))
    await insert_disposal(app, me.id, 200, days_ago(5))
    await insert_disposal(app, other, 999, days_ago(2))

    resp = await client.get("/api/disposals")

    assert resp.status_code == 200
    assert [item["volume_ml"] for item in resp.json()["items"]] == [300, 200, 100]


async def test_list_respects_limit(client, app, login):
    me = login()
    for index in range(5):
        await insert_disposal(app, me.id, 100 + index, days_ago(index))

    resp = await client.get("/api/disposals", params={"limit": 2})

    assert resp.status_code == 200
    assert [item["volume_ml"] for item in resp.json()["items"]] == [100, 101]


@pytest.mark.parametrize("limit", [0, 201])
async def test_list_invalid_limit(client, login, limit):
    login()

    resp = await client.get("/api/disposals", params={"limit": limit})

    assert resp.status_code == 422


# exclusão


async def test_delete_own(client, app, login):
    me = login()
    disposal_id = await insert_disposal(app, me.id, 500)

    resp = await client.delete(f"/api/disposals/{disposal_id}")

    assert resp.status_code == 204
    assert await app.state.db[DISPOSALS].count_documents({}) == 0


async def test_delete_other_users_disposal_is_404(client, app, login):
    login()
    disposal_id = await insert_disposal(app, str(ObjectId()), 500)

    resp = await client.delete(f"/api/disposals/{disposal_id}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert await app.state.db[DISPOSALS].count_documents({}) == 1


@pytest.mark.parametrize("disposal_id", ["id-invalido", str(ObjectId())])
async def test_delete_not_found(client, login, disposal_id):
    login()

    resp = await client.delete(f"/api/disposals/{disposal_id}")

    assert resp.status_code == 404


# impacto


async def test_impact_5_liters(client, app, login):
    login()
    await client.post("/api/disposals", json={"volume_ml": 2000})
    await client.post("/api/disposals", json={"volume_ml": 3000})
    await insert_disposal(app, str(ObjectId()), 7000)  # de outra pessoa: não conta

    resp = await client.get("/api/disposals/impact", params={"period": "all"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["period"] == "all"
    assert body["total_ml"] == 5000
    assert body["count"] == 2
    assert body["water_l"] == 5_000_000
    assert body["co2e_kg"] == 5.0
    assert body["factors_version"] == "2026-10"
    assert body["factors"] == {"water_l_per_l_oluc": 1_000_000, "co2e_kg_per_l_oluc": 1.0}
    assert set(body["sources"]) == {"water_l_per_l_oluc", "co2e_kg_per_l_oluc"}
    assert all(body["sources"].values())


async def test_impact_without_disposals_is_zero(client, login):
    login()

    resp = await client.get("/api/disposals/impact", params={"period": "all"})

    assert resp.status_code == 200
    body = resp.json()
    assert (body["total_ml"], body["count"], body["water_l"], body["co2e_kg"]) == (0, 0, 0, 0.0)


@pytest.mark.parametrize(
    ("period", "expected_count"),
    [("30d", 1), ("6m", 2), ("12m", 3), ("all", 4)],
)
async def test_impact_period_filter(client, app, login, period, expected_count):
    me = login()
    for days in (10, 100, 200, 400):
        await insert_disposal(app, me.id, 1000, days_ago(days))

    resp = await client.get("/api/disposals/impact", params={"period": period})

    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == expected_count
    assert body["total_ml"] == expected_count * 1000


async def test_impact_invalid_period(client, login):
    login()

    resp = await client.get("/api/disposals/impact", params={"period": "5y"})

    assert resp.status_code == 422


async def test_no_impact_numbers_are_stored(client, app, login):
    login()
    resp = await client.post("/api/disposals", json={"volume_ml": 1000})

    saved = await app.state.db[DISPOSALS].find_one({"_id": ObjectId(resp.json()["id"])})

    assert set(saved) == {"_id", "user_id", "point_id", "point_name", "volume_ml", "created_at"}


# lembrete



async def test_reminder_none(client, login):
    login(interval=180)

    resp = await client.get("/api/disposals/reminder")

    assert resp.status_code == 200
    assert resp.json() == {
        "status": "none",
        "last_disposal_at": None,
        "days_since": None,
        "days_left": None,
        "reminder_interval_days": 180,
    }


@pytest.mark.parametrize(
    ("interval", "days", "status"),
    [
        (180, 0, "ok"),
        (180, 143, "ok"),
        (180, 144, "soon"),  # 80% de 180
        (180, 179, "soon"),
        (180, 180, "overdue"),  # 100%
        (180, 400, "overdue"),
        (90, 71, "ok"),
        (90, 72, "soon"),  # 80% de 90
        (90, 89, "soon"),
        (90, 90, "overdue"),
        (365, 291, "ok"),
        (365, 292, "soon"),  # 80% de 365
        (365, 365, "overdue"),
    ],
)
async def test_reminder_status(client, app, login, interval, days, status):
    me = login(interval=interval)
    await insert_disposal(app, me.id, 1000, days_ago(days))

    resp = await client.get("/api/disposals/reminder")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == status
    assert body["days_since"] == days
    assert body["days_left"] == interval - days
    assert body["reminder_interval_days"] == interval
    assert body["last_disposal_at"]


async def test_reminder_uses_last_disposal(client, app, login):
    me = login(interval=180)
    await insert_disposal(app, me.id, 1000, days_ago(300))
    await insert_disposal(app, me.id, 1000, days_ago(10))
    await insert_disposal(app, str(ObjectId()), 1000, days_ago(1))  # de outra pessoa

    resp = await client.get("/api/disposals/reminder")

    assert resp.json()["days_since"] == 10
    assert resp.json()["status"] == "ok"
