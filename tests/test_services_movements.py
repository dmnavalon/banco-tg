from __future__ import annotations

import pytest

from src.services import movements
from src.services.exceptions import (
    InvalidTransition,
    MovementNotFound,
    ValidationError,
    VersionConflict,
)


def _stub_sync(monkeypatch):
    """Reemplaza sync_approved_movement_to_sheet por un no-op para que los
    tests de approve no necesiten un GSheet real. Devuelve la lista de movs
    con los que se llamó al sync para que el test pueda inspeccionarla."""
    calls: list[str] = []

    def _fake(mov_id, *, actor="system", source="system"):
        calls.append(mov_id)
        from src import db
        return db.get_movement_by_id(mov_id) or {}

    monkeypatch.setattr(movements, "sync_approved_movement_to_sheet", _fake)
    return calls


def test_approve_pending_to_approved(insert_pending, monkeypatch, fs):
    sync_calls = _stub_sync(monkeypatch)
    insert_pending("m1")

    out = movements.approve_movement(
        "m1", actor="diego", source="dashboard", expected_version=1,
    )

    assert out["review_status"] == "approved"
    assert out["sheet_sync_status"] == "pending_sync"
    assert out["version"] == 2
    assert out["decided_by"] == "diego"
    assert out["last_action_source"] == "dashboard"
    assert out["status"] == "aprobado"  # dual-write legacy
    assert out["final_category"] == "Transporte"  # fallback a suggested_category
    assert sync_calls == ["m1"]


def test_approve_already_approved_is_idempotent(insert_pending, monkeypatch, fs):
    _stub_sync(monkeypatch)
    insert_pending("m1", review_status="approved", sheet_sync_status="synced", version=2)

    out = movements.approve_movement("m1", actor="diego", source="dashboard")

    # No cambia el doc, retorna el actual.
    assert out["review_status"] == "approved"
    assert out["version"] == 2


def test_approve_with_stale_version_raises_409(insert_pending, monkeypatch, fs):
    _stub_sync(monkeypatch)
    insert_pending("m1", version=3)

    with pytest.raises(VersionConflict) as exc_info:
        movements.approve_movement("m1", actor="diego", source="dashboard", expected_version=1)

    assert exc_info.value.expected == 1
    assert exc_info.value.current == 3
    assert exc_info.value.current_doc.get("review_status") == "pending"


def test_approve_from_telegram_skips_version_check(insert_pending, monkeypatch, fs):
    _stub_sync(monkeypatch)
    insert_pending("m1", version=5)

    # Bot pasa expected_version=None — no debe fallar.
    out = movements.approve_movement(
        "m1", actor="123456", source="telegram", expected_version=None,
    )
    assert out["review_status"] == "approved"
    assert out["version"] == 6


def test_correct_pending_to_corrected_pending(insert_pending, fs):
    insert_pending("m1")

    out = movements.correct_movement(
        "m1", actor="diego", source="dashboard", expected_version=1,
        final_category="Hogar y alimentación", final_subcategory="Supermercado",
    )

    assert out["review_status"] == "corrected_pending"
    assert out["sheet_sync_status"] == "not_ready"
    assert out["version"] == 2
    assert out["final_category"] == "Hogar y alimentación"
    assert out["final_subcategory"] == "Supermercado"
    assert out["corrected_by"] == "diego"
    assert out["status"] == "pendiente"  # legacy mapeo


def test_correct_persists_reclassification_fields(insert_pending, fs):
    insert_pending("m1")

    out = movements.correct_movement(
        "m1", actor="diego", source="telegram", expected_version=None,
        suggested_category="Niños", suggested_subcategory="Juguetes",
        confidence=0.42, classifier_source="agent",
        comercio="Falabella Juguetes", tipo="Egreso",
        requiere_revision=True, pregunta_sugerida="¿es regalo?",
    )

    assert out["suggested_category"] == "Niños"
    assert out["suggested_subcategory"] == "Juguetes"
    assert out["confidence"] == 0.42
    assert out["comercio"] == "Falabella Juguetes"
    assert out["requiere_revision"] is True


def test_approve_correction_dispatches_sync(insert_pending, monkeypatch, fs):
    sync_calls = _stub_sync(monkeypatch)
    insert_pending("m1", review_status="corrected_pending", version=2)

    out = movements.approve_corrected_movement(
        "m1", actor="diego", source="dashboard", expected_version=2,
    )

    assert out["review_status"] == "corrected_approved"
    assert out["sheet_sync_status"] == "pending_sync"
    assert out["version"] == 3
    assert sync_calls == ["m1"]


def test_approve_correction_invalid_transition(insert_pending, monkeypatch, fs):
    _stub_sync(monkeypatch)
    insert_pending("m1")  # review_status=pending

    with pytest.raises(InvalidTransition):
        movements.approve_corrected_movement("m1", actor="diego", source="dashboard")


def test_ignore_with_empty_reason_raises_validation(insert_pending, fs):
    insert_pending("m1")

    with pytest.raises(ValidationError):
        movements.ignore_movement("m1", actor="diego", source="dashboard", reason="   ")


def test_ignore_pending(insert_pending, fs):
    insert_pending("m1")

    out = movements.ignore_movement(
        "m1", actor="diego", source="dashboard", reason="duplicado",
        expected_version=1,
    )

    assert out["review_status"] == "ignored"
    assert out["sheet_sync_status"] == "not_ready"
    assert out["ignore_reason"] == "duplicado"
    assert out["status"] == "ignorado"


def test_reopen_from_ignored_to_pending(insert_pending, fs):
    insert_pending("m1", review_status="ignored", sheet_sync_status="not_ready", version=3)

    out = movements.reopen_movement(
        "m1", actor="diego", source="dashboard", expected_version=3,
    )

    assert out["review_status"] == "pending"
    assert out["sheet_sync_status"] == "not_ready"
    assert out["version"] == 4


def test_reopen_from_approved_clears_sync(insert_pending, fs):
    insert_pending("m1", review_status="approved", sheet_sync_status="synced", version=2)

    out = movements.reopen_movement(
        "m1", actor="diego", source="telegram", expected_version=None,
    )

    assert out["review_status"] == "pending"
    assert out["sheet_sync_status"] == "not_ready"


def test_movement_not_found(fs):
    with pytest.raises(MovementNotFound):
        movements.approve_movement("nope", actor="diego", source="dashboard")


def test_bulk_approve_partial_failure(insert_pending, monkeypatch, fs):
    sync_calls = _stub_sync(monkeypatch)
    insert_pending("m1", version=1)
    insert_pending("m2", version=1)
    insert_pending("m3", review_status="approved", version=2)  # ya aprobado: idempotente

    versions = {"m1": 1, "m2": 99, "m3": 2}  # m2: stale
    results = movements.bulk_approve(
        ["m1", "m2", "m3"],
        actor="diego", source="dashboard",
        versions=versions,
    )

    assert results["m1"]["status"] == "ok"
    assert results["m2"]["status"] == "conflict"
    assert results["m3"]["status"] == "ok"  # idempotente
    # m1 dispara sync, m3 no porque no cambió.
    assert sync_calls == ["m1"]


def test_bulk_categorize(insert_pending, fs):
    insert_pending("m1")
    insert_pending("m2")

    results = movements.bulk_categorize(
        ["m1", "m2"],
        actor="diego", source="dashboard",
        final_category="Hogar y alimentación", final_subcategory="Supermercado",
        versions={"m1": 1, "m2": 1},
    )

    assert results["m1"]["status"] == "ok"
    assert results["m2"]["status"] == "ok"
    assert results["m1"]["movement"]["review_status"] == "corrected_pending"
    assert results["m2"]["movement"]["final_category"] == "Hogar y alimentación"


def test_bulk_ignore_requires_reason(insert_pending, fs):
    insert_pending("m1")
    with pytest.raises(ValidationError):
        movements.bulk_ignore(["m1"], actor="diego", source="dashboard", reason="")


def test_bulk_comment_no_status_change(insert_pending, fs):
    insert_pending("m1", review_status="approved", version=2)

    results = movements.bulk_comment(
        ["m1"], actor="diego", source="dashboard",
        comment="revisar después", versions={"m1": 2},
    )

    assert results["m1"]["status"] == "ok"
    mov = results["m1"]["movement"]
    assert mov["comment"] == "revisar después"
    assert mov["review_status"] == "approved"  # no cambió
    assert mov["version"] == 3
