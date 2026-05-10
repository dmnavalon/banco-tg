from __future__ import annotations

import pytest


@pytest.fixture
def api_client(fs, monkeypatch):
    monkeypatch.setenv("ENABLE_MOVIMIENTOS_REVIEW", "true")
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    from src.api import server
    app = server.create_app()
    return app.test_client()


def _auth():
    return {"Authorization": "Bearer test-token"}


def test_db_add_and_list_taxonomy_overrides(fs):
    from src import db
    db.add_taxonomy_override("Hobbies", "Modelismo")
    db.add_taxonomy_override("Hobbies", "Pintura")
    db.add_taxonomy_override("Hobbies", "Modelismo")  # idempotente

    out = db.list_taxonomy_overrides()
    assert "Hobbies" in out
    assert sorted(out["Hobbies"]) == ["Modelismo", "Pintura"]


def test_get_taxonomy_merges_base_and_overrides(fs, monkeypatch):
    from src import classifier, db
    classifier.invalidate_taxonomy_cache()
    db.add_taxonomy_override("Hobbies", "Modelismo")
    # Sub nueva dentro de una cat existente (no debe duplicar las que ya estaban).
    db.add_taxonomy_override("Hogar y alimentación", "Mercado orgánico")

    tax = classifier.get_taxonomy()
    assert "Hobbies" in tax and "Modelismo" in tax["Hobbies"]
    assert "Mercado orgánico" in tax["Hogar y alimentación"]
    assert "Supermercado" in tax["Hogar y alimentación"]  # base intacta


def test_post_categories_creates_then_idempotent(api_client, fs):
    from src import classifier
    classifier.invalidate_taxonomy_cache()

    r1 = api_client.post(
        "/api/categories",
        json={"cat": "Hobbies", "sub": "Modelismo"},
        headers=_auth(),
    )
    assert r1.status_code == 201
    body1 = r1.get_json()
    assert body1["created"] is True
    assert "Hobbies" in body1["taxonomy"]
    assert "Modelismo" in body1["taxonomy"]["Hobbies"]

    # Mismo POST → 200, created=False.
    r2 = api_client.post(
        "/api/categories",
        json={"cat": "Hobbies", "sub": "Modelismo"},
        headers=_auth(),
    )
    assert r2.status_code == 200
    body2 = r2.get_json()
    assert body2["created"] is False


def test_post_categories_validations(api_client, fs):
    from src import classifier
    classifier.invalidate_taxonomy_cache()

    # cat vacía
    r = api_client.post("/api/categories", json={"cat": " ", "sub": "X"}, headers=_auth())
    assert r.status_code == 422

    # sub vacía
    r = api_client.post("/api/categories", json={"cat": "Hobbies", "sub": ""}, headers=_auth())
    assert r.status_code == 422

    # cat de ingreso (prohibido para creación libre)
    r = api_client.post(
        "/api/categories",
        json={"cat": "Sueldo", "sub": "Bono extra"},
        headers=_auth(),
    )
    assert r.status_code == 422

    # cat demasiado larga
    r = api_client.post(
        "/api/categories",
        json={"cat": "X" * 100, "sub": "Y"},
        headers=_auth(),
    )
    assert r.status_code == 422


def test_post_categories_match_normaliza_tildes(api_client, fs):
    from src import classifier
    classifier.invalidate_taxonomy_cache()

    # "Educación / Colegio" ya existe en _BASE_TAXONOMY. Aceptar variante sin tilde
    # debe dar created=False (match normalizado).
    r = api_client.post(
        "/api/categories",
        json={"cat": "Educacion", "sub": "Colegio"},
        headers=_auth(),
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["created"] is False
