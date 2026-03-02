from pathlib import Path

from teaming24.data.database import Database


def _build_db(tmp_path: Path) -> Database:
    return Database(db_path=tmp_path / "teaming24-consistency.db")


def test_payment_records_are_idempotent(tmp_path: Path) -> None:
    db = _build_db(tmp_path)

    assert db.is_payment_recorded(parent_task_id="task-1", requester_id="node-a") is False

    db.save_payment_record(parent_task_id="task-1", requester_id="node-a")
    db.save_payment_record(parent_task_id="task-1", requester_id="node-a")

    assert db.is_payment_recorded(parent_task_id="task-1", requester_id="node-a") is True

    with db._get_conn() as conn:  # noqa: SLF001 - test-only internal verification
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM payment_records
            WHERE parent_task_id = ? AND requester_id = ?
            """,
            ("task-1", "node-a"),
        ).fetchone()
    assert int(row["cnt"]) == 1


def test_wallet_expense_records_are_idempotent(tmp_path: Path) -> None:
    db = _build_db(tmp_path)

    assert db.is_expense_recorded(task_id="task-2", target_an="remote-an-1") is False

    db.save_expense_record(task_id="task-2", target_an="remote-an-1", amount=1.25)
    db.save_expense_record(task_id="task-2", target_an="remote-an-1", amount=3.75)

    assert db.is_expense_recorded(task_id="task-2", target_an="remote-an-1") is True

    with db._get_conn() as conn:  # noqa: SLF001 - test-only internal verification
        row = conn.execute(
            """
            SELECT amount, COUNT(*) AS cnt
            FROM wallet_expense_records
            WHERE task_id = ? AND target_an = ?
            """,
            ("task-2", "remote-an-1"),
        ).fetchone()
    assert int(row["cnt"]) == 1
    assert float(row["amount"]) == 3.75
