from datetime import datetime, timezone


def create_task(conn, *, agent_id: int, title: str, description: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO tasks (agent_id, title, description, created_at) VALUES (?, ?, ?, ?)",
        (agent_id, title, description, now),
    )
    return get_task(conn, cur.lastrowid)


def get_task(conn, task_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(conn, *, agent_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM tasks WHERE agent_id = ? ORDER BY id DESC", (agent_id,)
    ).fetchall()
    return [dict(r) for r in rows]
