"""
state.py — SQLite-backed state tracker for email deduplication.

Tracks which emails have already been processed and which calendar
events have been created, preventing duplicates across runs.
"""

import sqlite3
from datetime import datetime

import config


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, column_type: str
) -> None:
    """Add a column if it does not already exist."""
    columns = {
        row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column in columns:
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def _get_db() -> sqlite3.Connection:
    """Get a connection to the state database, creating tables if needed."""
    conn = sqlite3.connect(config.DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_emails (
            email_id        TEXT PRIMARY KEY,
            subject         TEXT,
            processed_at    TEXT NOT NULL,
            calendar_event_id TEXT
        )
    """)
    _ensure_column(conn, "processed_emails", "event_date", "TEXT")
    _ensure_column(conn, "processed_emails", "canonical_event_key", "TEXT")
    _ensure_column(conn, "processed_emails", "calendar_action", "TEXT")
    _ensure_column(conn, "processed_emails", "eligibility_reason", "TEXT")
    _ensure_column(conn, "processed_emails", "classification_role", "TEXT")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time       TEXT NOT NULL,
            emails_scanned  INTEGER DEFAULT 0,
            events_created  INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def is_processed(email_id: str) -> bool:
    """Check if an email has already been processed."""
    conn = _get_db()
    cursor = conn.execute(
        "SELECT 1 FROM processed_emails WHERE email_id = ?", (email_id,)
    )
    result = cursor.fetchone() is not None
    conn.close()
    return result


def mark_processed(
    email_id: str,
    subject: str,
    calendar_event_id: str | None = None,
    event_date: str = "",
    canonical_event_key: str = "",
    calendar_action: str = "",
    eligibility_reason: str = "",
    classification_role: str = "",
) -> None:
    """Mark an email as processed, optionally with the created calendar event ID."""
    conn = _get_db()
    conn.execute(
        """INSERT OR REPLACE INTO processed_emails
           (email_id, subject, processed_at, calendar_event_id, event_date,
            canonical_event_key, calendar_action, eligibility_reason,
            classification_role)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            email_id,
            subject,
            datetime.now().isoformat(),
            calendar_event_id,
            event_date,
            canonical_event_key,
            calendar_action,
            eligibility_reason,
            classification_role,
        ),
    )
    conn.commit()
    conn.close()


def log_scan(emails_scanned: int, events_created: int) -> None:
    """Log a scan run's summary statistics."""
    conn = _get_db()
    conn.execute(
        """INSERT INTO scan_log (scan_time, emails_scanned, events_created)
           VALUES (?, ?, ?)""",
        (datetime.now().isoformat(), emails_scanned, events_created),
    )
    conn.commit()
    conn.close()


def get_last_scan_time() -> str | None:
    """Get the timestamp of the last scan, or None if never scanned."""
    conn = _get_db()
    cursor = conn.execute(
        "SELECT scan_time FROM scan_log ORDER BY id DESC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None
