from __future__ import annotations

from src.services import audit, movements


def test_record_event_creates_doc(fs):
    audit.record_event(
        movement_id="m1",
        action="approved_from_dashboard",
        prev_review_status="pending",
        new_review_status="approved",
        prev_sheet_sync_status="not_ready",
        new_sheet_sync_status="pending_sync",
        actor="diego",
        source="dashboard",
        details={"final_category": "Otros"},
    )

    docs = list(fs.collection("movement_audit").stream())
    assert len(docs) == 1
    e = docs[0].to_dict()
    assert e["movement_id"] == "m1"
    assert e["action"] == "approved_from_dashboard"
    assert e["prev_review_status"] == "pending"
    assert e["new_review_status"] == "approved"
    assert e["actor"] == "diego"
    assert e["source"] == "dashboard"
    assert e["details"] == {"final_category": "Otros"}
    assert "created_at" in e


def test_audit_event_recorded_after_approve(insert_pending, monkeypatch, fs):
    # No queremos que sync llame a GSheet en el test.
    monkeypatch.setattr(movements, "sync_approved_movement_to_sheet", lambda *a, **k: {})
    insert_pending("m1")

    movements.approve_movement("m1", actor="diego", source="dashboard", expected_version=1)

    events = audit.list_for_movement("m1")
    assert len(events) == 1
    assert events[0]["action"] == "approved_from_dashboard"
    assert events[0]["new_review_status"] == "approved"


def test_audit_list_orders_desc(fs):
    for i, ts in enumerate(["2026-05-01 09:00:00", "2026-05-02 09:00:00", "2026-05-03 09:00:00"]):
        ref = fs.collection("movement_audit").document(f"e{i}")
        ref.set({
            "movement_id": "m1",
            "action": f"a{i}",
            "prev_review_status": "pending",
            "new_review_status": "approved",
            "prev_sheet_sync_status": "not_ready",
            "new_sheet_sync_status": "synced",
            "actor": "diego",
            "source": "dashboard",
            "details": {},
            "created_at": ts,
        })

    events = audit.list_for_movement("m1")
    assert [e["action"] for e in events] == ["a2", "a1", "a0"]
