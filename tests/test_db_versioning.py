from __future__ import annotations

from src import db


def test_query_movements_by_review_status(fs, insert_pending):
    insert_pending("m1", review_status="pending")
    insert_pending("m2", review_status="approved")
    insert_pending("m3", review_status="ignored")

    pending = db.query_movements(review_status="pending")
    assert {m["id"] for m in pending} == {"m1"}

    approved = db.query_movements(review_status=["approved", "corrected_approved"])
    assert {m["id"] for m in approved} == {"m2"}


def test_query_movements_by_amount_range(fs, insert_pending):
    insert_pending("m1", amount=-1000.0)
    insert_pending("m2", amount=-50000.0)
    insert_pending("m3", amount=-10.0)

    big = db.query_movements(min_amount=5000)
    assert {m["id"] for m in big} == {"m2"}

    small = db.query_movements(max_amount=100)
    assert {m["id"] for m in small} == {"m3"}


def test_query_movements_description_contains(fs, insert_pending):
    insert_pending("m1", description="COMPRA UBER")
    insert_pending("m2", description="LIDER SUPERMERCADO")
    insert_pending("m3", description="UBER EATS")

    out = db.query_movements(description_contains="uber")
    assert {m["id"] for m in out} == {"m1", "m3"}


def test_get_movement_by_id(fs, insert_pending):
    insert_pending("m1")
    mov = db.get_movement_by_id("m1")
    assert mov is not None
    assert mov["id"] == "m1"

    assert db.get_movement_by_id("nope") is None
