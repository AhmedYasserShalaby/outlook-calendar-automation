"""
config.py — Configuration loader for Email → Calendar scanner.

Loads environment variables from .env and course schedule from courses.json.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_DIR = Path(__file__).parent
load_dotenv(_PROJECT_DIR / ".env")


# ── Google Calendar ──────────────────────────────────────────────────
GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")
GOOGLE_CREDENTIALS_PATH: str = str(_PROJECT_DIR / "client_secret_1068366550113-44p26ttgibecqejfcudmajkr26jh4d5g.apps.googleusercontent.com.json")
GOOGLE_TOKEN_PATH: str = str(_PROJECT_DIR / "token.json")

# ── OpenAI ───────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# ── Scanner Settings ────────────────────────────────────────────────
SCAN_WINDOW_HOURS: int = int(os.getenv("SCAN_WINDOW_HOURS", "24"))
RESCAN_OVERLAP_HOURS: int = int(os.getenv("RESCAN_OVERLAP_HOURS", "2"))
TIMEZONE: str = os.getenv("TIMEZONE", "Africa/Cairo")
UNIVERSITY_DOMAIN: str = os.getenv("UNIVERSITY_DOMAIN", "")
DRY_RUN: bool = os.getenv("DRY_RUN", "false").lower() == "true"

# ── Outlook Configuration ────────────────────────────────────────────
OUTLOOK_EMAIL: str = os.getenv("OUTLOOK_EMAIL", "")
OUTLOOK_PASSWORD: str = os.getenv("OUTLOOK_PASSWORD", "")
BROWSER_STATE_DIR: str = str(_PROJECT_DIR / "browser_state")

# ── State ────────────────────────────────────────────────────────────
DB_PATH: str = str(_PROJECT_DIR / "processed_emails.db")
AUDIT_LOG_DIR: str = str(_PROJECT_DIR / "audit_logs")
RAW_EMAIL_LOG_DIR: str = str(_PROJECT_DIR / "raw_email_logs")
CRAWL_MAX_ROUNDS: int = int(os.getenv("CRAWL_MAX_ROUNDS", "80"))

# ── University Slot Schedule ─────────────────────────────────────────
SLOT_TIMES: dict[int, tuple[str, str]] = {
    1: ("08:30", "10:00"),
    2: ("10:15", "11:45"),
    3: ("12:00", "13:30"),
    4: ("13:45", "15:15"),
    5: ("15:45", "17:15"),
}


# ── Course Schedule ─────────────────────────────────────────────────
def load_courses() -> list[dict]:
    """Load course schedule from courses.json."""
    courses_path = _PROJECT_DIR / "courses.json"
    if not courses_path.exists():
        raise FileNotFoundError(
            f"courses.json not found at {courses_path}. "
            "Copy courses.json.example and fill in your schedule."
        )
    with open(courses_path, "r") as f:
        data = json.load(f)
    return data.get("courses", [])


def get_course_by_code(code: str, courses: list[dict]) -> dict | None:
    """Look up a course by its code (case-insensitive)."""
    code_upper = code.upper().strip()
    for course in courses:
        if course["code"].upper().strip() == code_upper:
            return course
    return None


def get_slot_time(slot_number: int) -> tuple[str, str] | None:
    """Look up the configured start/end times for a university slot number."""
    return SLOT_TIMES.get(slot_number)
