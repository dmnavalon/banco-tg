from __future__ import annotations

import os

import pytest


@pytest.fixture
def api_client(fs, monkeypatch):
    """Flask test client con la API en modo movimientos+auth, usando el
    MockFirestore del fixture `fs`. La auth se valida con un token fake
    inyectado en env."""
    monkeypatch.setenv("ENABLE_MOVIMIENTOS_REVIEW", "true")
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    # Stub del sync para no llamar GSheet: devolvemos el doc actual de
    # Firestore (mock), simulando un sync exitoso sin tocar la red. La capa
    # de servicios setea sheet_sync_status=pending_sync ANTES del sync; aquí
    # lo dejamos así (no marcamos synced) — los tests no validan ese paso.
    from src import db as _db
    from src.services import movements
    monkeypatch.setattr(
        movements, "sync_approved_movement_to_sheet",
        lambda mov_id, **kw: _db.get_movement_by_id(mov_id) or {},
    )

    # create_app importa server.run_app — solo necesitamos la app, no el thread.
    from src.api import server
    app = server.create_app()
    assert app is not None
    return app.test_client()


def _auth():
    return {"Authorization": "Bearer test-token"}


def test_health_does_not_require_auth(api_client):
    r = api_client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_endpoints_require_token(api_client):
    r = api_client.get("/api/movements")
    assert r.status_code == 401


def test_list_movements_filters_by_status(api_client, insert_pending):
    insert_pending("m1", review_status="pending")
    insert_pending("m2", review_status="approved")
    insert_pending("m3", review_status="ignored")

    r = api_client.get("/api/movements?status=pending", headers=_auth())
    assert r.status_code == 200
    items = r.get_json()["items"]
    assert {it["id"] for it in items} == {"m1"}


def test_list_movements_missing_index_returns_422(api_client, monkeypatch):
    """Si Firestore exige un índice compuesto que no existe, el endpoint debe
    responder 422 con el mensaje (incluye el link de creación), no un 500."""
    from google.api_core import exceptions as gax_exceptions

    from src import db as _db

    def _boom(**kwargs):
        raise gax_exceptions.FailedPrecondition(
            "The query requires an index. You can create it here: "
            "https://console.firebase.google.com/v1/r/project/control-gastos-c53b6/firestore/indexes?create_composite=..."
        )

    monkeypatch.setattr(_db, "query_movements", _boom)
    r = api_client.get(
        "/api/movements?status=approved&from=2026-05-01&to=2026-05-31",
        headers=_auth(),
    )
    assert r.status_code == 422
    body = r.get_json()
    assert body["error"] == "missing_index"
    assert "requires an index" in body["message"]
    assert "create_composite" in body["message"]


def test_approve_endpoint(api_client, insert_pending):
    insert_pending("m1")
    r = api_client.post(
        "/api/movements/m1/approve",
        json={"version": 1, "actor": "diego"},
        headers=_auth(),
    )
    assert r.status_code == 200
    mov = r.get_json()["movement"]
    assert mov["review_status"] == "approved"
    assert mov["version"] == 2


def test_approve_version_conflict(api_client, insert_pending):
    insert_pending("m1", version=5)
    r = api_client.post(
        "/api/movements/m1/approve",
        json={"version": 1, "actor": "diego"},
        headers=_auth(),
    )
    assert r.status_code == 409
    body = r.get_json()
    assert body["error"] == "version_conflict"
    assert body["expected"] == 1
    assert body["current"] == 5


def test_ignore_requires_reason(api_client, insert_pending):
    insert_pending("m1")
    r = api_client.post(
        "/api/movements/m1/ignore",
        json={"version": 1, "reason": "  "},
        headers=_auth(),
    )
    assert r.status_code == 422


def test_categories_endpoint(api_client):
    r = api_client.get("/api/categories", headers=_auth())
    assert r.status_code == 200
    body = r.get_json()
    assert "taxonomy" in body
    assert "Sueldo" in body["taxonomy"]


def test_bulk_approve_partial(api_client, insert_pending):
    insert_pending("m1", version=1)
    insert_pending("m2", version=1)

    r = api_client.post(
        "/api/movements/bulk/approve",
        json={"ids": ["m1", "m2"], "versions": {"m1": 1, "m2": 99}},
        headers=_auth(),
    )
    assert r.status_code == 200
    res = r.get_json()["results"]
    assert res["m1"]["status"] == "ok"
    assert res["m2"]["status"] == "conflict"


def test_audit_endpoint(api_client, insert_pending):
    insert_pending("m1")
    api_client.post(
        "/api/movements/m1/approve",
        json={"version": 1, "actor": "diego"},
        headers=_auth(),
    )
    r = api_client.get("/api/movements/m1/audit", headers=_auth())
    assert r.status_code == 200
    events = r.get_json()["events"]
    assert any(e["action"] == "approved_from_dashboard" for e in events)
