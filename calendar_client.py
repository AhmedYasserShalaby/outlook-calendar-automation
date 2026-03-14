"""
calendar_client.py — Google Calendar event creator.

Uses shared Google OAuth2 credentials (from gmail_client) to create
events based on classified email data.
"""

import logging
from datetime import datetime, timedelta

from googleapiclient.discovery import build

import config
import gmail_client

logger = logging.getLogger(__name__)

# Google Calendar color IDs (1-11)
# 1=Lavender, 2=Sage, 3=Grape, 4=Flamingo, 5=Banana
# 6=Tangerine, 7=Peacock, 8=Graphite, 9=Blueberry, 10=Basil, 11=Tomato
DEFAULT_COLOR = "7"  # Peacock
REMINDER_CATEGORY_COLORS = {
    "bring": "11",     # Tomato
    "install": "6",    # Tangerine
    "download": "5",   # Banana
    "prepare": "10",   # Basil
    "submit": "9",     # Blueberry
    "review": "3",     # Grape
    "other": "8",      # Graphite
}


def _get_service():
    """Build and return the Google Calendar API service using shared creds."""
    creds = gmail_client.get_credentials()
    return build("calendar", "v3", credentials=creds)


def _check_duplicate(
    service,
    title: str,
    date: str,
    calendar_id: str,
    canonical_event_key: str = "",
) -> bool:
    """Check if a similar event already exists on or near the given date."""
    try:
        event = find_event_by_canonical_key(
            service,
            canonical_event_key,
            date,
            calendar_id,
        )
        if event:
            logger.info(
                "Duplicate found by canonical key: %s on %s",
                event.get("summary", ""),
                date,
            )
            return True

        time_min = f"{date}T00:00:00Z"
        time_max = f"{date}T23:59:59Z"

        events_result = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            q=title[:30],  # Search by partial title
            singleEvents=True,
        ).execute()

        events = events_result.get("items", [])
        for event in events:
            if title.lower() in event.get("summary", "").lower():
                logger.info(f"Duplicate found: {event['summary']} on {date}")
                return True
    except Exception as e:
        logger.warning(f"Duplicate check failed: {e}")

    return False


def _description_has_canonical_key(description: str, canonical_event_key: str) -> bool:
    """Return True when the event description contains the canonical key."""
    if not canonical_event_key or not description:
        return False
    return f'"canonical_event_key": "{canonical_event_key}"' in description


def find_event_by_canonical_key(
    service,
    canonical_event_key: str,
    date: str,
    calendar_id: str,
    days_radius: int = 14,
):
    """Find an existing event carrying the same canonical key."""
    if not canonical_event_key or not date:
        return None

    try:
        center = datetime.strptime(date, "%Y-%m-%d")
        time_min = (center - timedelta(days=days_radius)).strftime("%Y-%m-%dT00:00:00Z")
        time_max = (center + timedelta(days=days_radius)).strftime("%Y-%m-%dT23:59:59Z")
        page_token = None

        while True:
            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                pageToken=page_token,
            ).execute()
            for event in events_result.get("items", []):
                if _description_has_canonical_key(
                    event.get("description", ""), canonical_event_key
                ):
                    return event
            page_token = events_result.get("nextPageToken")
            if not page_token:
                break
    except Exception as e:
        logger.warning(f"Canonical duplicate lookup failed: {e}")

    return None


def has_event_with_canonical_key(
    canonical_event_key: str,
    date: str,
    calendar_id: str = "",
) -> bool:
    """Return True when an event with the canonical key already exists."""
    service = _get_service()
    calendar_id = calendar_id or config.GOOGLE_CALENDAR_ID
    return (
        find_event_by_canonical_key(service, canonical_event_key, date, calendar_id)
        is not None
    )


def get_color_for_category(category: str) -> str:
    """Return a stable color for reminder/event categories."""
    return REMINDER_CATEGORY_COLORS.get(category.lower().strip(), DEFAULT_COLOR)


def create_event(
    title: str,
    date: str,
    time_start: str = "",
    time_end: str = "",
    description: str = "",
    course_code: str = "",
    color_id: str = "",
    courses: list[dict] | None = None,
    canonical_event_key: str = "",
) -> str | None:
    """
    Create a Google Calendar event.

    Args:
        title: Event title (e.g., "[CSEN301] Midterm Exam")
        date: Event date as YYYY-MM-DD
        time_start: Start time as HH:MM (empty = all-day event)
        time_end: End time as HH:MM (empty = 1 hour after start or all-day)
        description: Event description
        course_code: Course code for color mapping
        color_id: Override Google Calendar color ID (1-11)
        courses: Course list for color lookup

    Returns:
        Created event ID, or None if failed/skipped
    """
    if not date:
        logger.warning(f"No date for event '{title}' — skipping")
        return None

    service = _get_service()
    calendar_id = config.GOOGLE_CALENDAR_ID

    # Build the full title — use course NAME (not code) for readability
    course_name = ""
    if course_code and courses:
        for c in courses:
            if c["code"].upper() == course_code.upper():
                course_name = c["name"]
                break
    prefix = f"[{course_name}] " if course_name else (f"[{course_code}] " if course_code else "")
    full_title = f"{prefix}{title}"

    # Check for duplicates
    if _check_duplicate(
        service, full_title, date, calendar_id, canonical_event_key=canonical_event_key
    ):
        logger.info(f"Skipping duplicate: {full_title}")
        return None

    # Determine color
    if not color_id and course_code and courses:
        for c in courses:
            if c["code"].upper() == course_code.upper():
                color_id = c.get("color", DEFAULT_COLOR)
                break
    if not color_id:
        color_id = DEFAULT_COLOR

    # Build event body
    if time_start:
        # Timed event
        start_dt = f"{date}T{time_start}:00"
        if time_end:
            end_dt = f"{date}T{time_end}:00"
        else:
            # Default to 1-hour duration
            start_obj = datetime.strptime(start_dt, "%Y-%m-%dT%H:%M:%S")
            end_obj = start_obj + timedelta(hours=1)
            end_dt = end_obj.strftime("%Y-%m-%dT%H:%M:%S")

        event_body = {
            "summary": full_title,
            "description": description,
            "start": {"dateTime": start_dt, "timeZone": config.TIMEZONE},
            "end": {"dateTime": end_dt, "timeZone": config.TIMEZONE},
            "colorId": color_id,
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 1440},   # 1 day before
                    {"method": "popup", "minutes": 60},      # 1 hour before
                ],
            },
        }
    else:
        # All-day event
        next_day = (
            datetime.strptime(date, "%Y-%m-%d") + timedelta(days=1)
        ).strftime("%Y-%m-%d")
        event_body = {
            "summary": full_title,
            "description": description,
            "start": {"date": date},
            "end": {"date": next_day},
            "colorId": color_id,
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 1440},   # 1 day before
                ],
            },
        }

    try:
        event = service.events().insert(
            calendarId=calendar_id, body=event_body
        ).execute()

        event_id = event.get("id", "")
        link = event.get("htmlLink", "")
        logger.info(f"✅ Created event: {full_title} → {link}")
        return event_id

    except Exception as e:
        logger.error(f"❌ Failed to create event '{full_title}': {e}")
        return None
