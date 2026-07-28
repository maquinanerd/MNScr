import sqlite3
from pathlib import Path

DEFAULT_TIMEOUT_S = 30
DEFAULT_BUSY_TIMEOUT_MS = 30000


def configure_connection(
    conn: sqlite3.Connection,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
    enable_wal: bool = True,
) -> sqlite3.Connection:
    conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if enable_wal:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass
    return conn


def connect_sqlite(
    db_path: str | Path,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    detect_types: int = 0,
    row_factory=None,
    enable_wal: bool = True,
) -> sqlite3.Connection:
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_file), detect_types=detect_types, timeout=timeout_s)
    if row_factory is not None:
        conn.row_factory = row_factory
    return configure_connection(conn, enable_wal=enable_wal)
