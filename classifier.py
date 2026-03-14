"""
classifier.py — AI-powered email classifier for academic events.

Sends email content to OpenAI (or falls back to keyword matching)
to determine if an email is about an exam, assignment, quiz, or
other actionable academic event. Returns structured extraction.
"""

import json
import logging
import re
from datetime import datetime, timedelta

from openai import OpenAI

import config

logger = logging.getLogger(__name__)


# ── Types ────────────────────────────────────────────────────────────


class ClassifiedEvent:
    """Structured result from email classification."""

    def __init__(
        self,
        is_actionable: bool,
        event_type: str = "",
        course_code: str = "",
        title: str = "",
        date: str = "",
        time_start: str = "",
        time_end: str = "",
        description: str = "",
        study_topics: list[str] | None = None,
        materials_needed: list[str] | None = None,
        reminders: list[dict] | None = None,
        confidence: float = 0.0,
        canonical_event_key: str = "",
        classification_role: str = "",
        eligibility_reason: str = "",
    ):
        self.is_actionable = is_actionable
        self.event_type = event_type  # exam | assignment | quiz | deadline | project | meeting | other
        self.course_code = course_code
        self.title = title
        self.date = date  # YYYY-MM-DD
        self.time_start = time_start  # HH:MM (24h) or empty for all-day
        self.time_end = time_end
        self.description = description
        self.study_topics = study_topics or []
        self.materials_needed = materials_needed or []
        self.reminders = reminders or []
        self.confidence = confidence
        self.canonical_event_key = canonical_event_key
        self.classification_role = classification_role
        self.eligibility_reason = eligibility_reason

    def __repr__(self) -> str:
        if not self.is_actionable:
            return "ClassifiedEvent(not actionable)"
        return (
            f"ClassifiedEvent({self.event_type} | {self.course_code} | "
            f"{self.title} | {self.date} {self.time_start})"
        )

    def to_dict(self) -> dict:
        return self.__dict__


# ── AI Classification (OpenAI) ──────────────────────────────────────

SYSTEM_PROMPT = """You are an academic email classifier for a university student. Your job is to catch EVERY quiz, exam, assignment, deadline, or important academic task from emails and create calendar events.

You MUST respond with a valid JSON object and nothing else. Use this exact schema:

{
  "is_actionable": true/false,
  "event_type": "exam" | "assignment" | "quiz" | "deadline" | "project" | "lab" | "meeting" | "makeup" | "presentation" | "other",
  "course_code": "e.g. INCS617 — extract from email, match to known courses",
  "title": "short descriptive title for the calendar event",
  "date": "YYYY-MM-DD — the date the event takes place or is due",
  "time_start": "HH:MM in 24h format, or empty string for all-day",
  "time_end": "HH:MM in 24h format, or empty string if unspecified",
  "description": "Detailed action items: What should the student DO or PREPARE? Include topics to study, materials needed, submission format, or what to bring. Be specific and practical.",
  "study_topics": ["List", "of", "very", "specific", "topics", "to", "study", "for", "this", "event"],
  "materials_needed": ["List", "of", "materials", "needed", "e.g.", "lectures", "labs", "syllabus", "textbook"],
  "reminders": [
    {
      "title": "short reminder title",
      "category": "bring" | "install" | "download" | "prepare" | "submit" | "review" | "other",
      "date": "YYYY-MM-DD",
      "time_start": "HH:MM in 24h format, or empty string for all-day",
      "time_end": "HH:MM in 24h format, or empty string if unspecified",
      "description": "Concrete reminder with the reason and any inferred context"
    }
  ],
  "confidence": 0.0 to 1.0
}

CRITICAL RULES — READ CAREFULLY:

1. **PRIORITIZE REAL CALENDAR ITEMS** — Mark `is_actionable=true` only for explicit academic actions such as quizzes, exams, deadlines, submissions, required presentations, compensations, makeups, rescheduled/cancelled sessions, or clearly required attendance events.
   - Emails that are only about references, materials, uploads, announcements, workshop promotion, registration, clarifications, or general preparation should usually be `is_actionable=false`.
   - If an email mainly adds context to an existing event (room, attachment, reminder, reference sheet, uploaded file), prefer `is_actionable=false`.

2. **ALWAYS provide a date** — Even if the email does NOT mention a specific date:
   - If completely unclear, default to the NEXT class session for that course (dates provided below).
   - NEVER leave date empty for actionable events.

3. **DATE INFERENCE** — The schedule below lists upcoming class dates for each course. Pick dates using ONLY these rules (in order):
   a. If the email contains an EXPLICIT date (e.g. "8/3/2026", "March 13th") → use that date exactly.
   b. If the email says "next week" → you MUST anchor "next week" relative to the EMAIL SENT DATE (provided below as "Email sent date"), NOT today's date. "Next week" means the 7-day period starting the Monday after the email was sent. Use the course's scheduled session that falls within that week.
   c. If the email says "this week" or "tomorrow" → anchor relative to the EMAIL SENT DATE, not today.
   d. For EVERYTHING ELSE (no date, "next session", "upcoming", "first N labs", etc.) → use the "This week" date from the schedule. DO NOT try to calculate or reason about when something should happen. The nearest session is ALWAYS the right choice.

   CRITICAL: Relative words like "next week", "tomorrow", "this week" are ALWAYS relative to when the EMAIL WAS SENT, not when you are processing it. Always use the "Email sent date" field to anchor these calculations.

4. **ALWAYS use the course schedule for timing** — For ANY in-class event (quiz, exam, assignment, lab, tutorial, meeting, compensation), you MUST use the exact time slot from the course schedule provided below for time_start and time_end. NEVER leave times empty for an in-class event. Only leave time_start empty for take-home deadlines or submission events where the student is not physically attending class.
   - If the email explicitly mentions a numbered slot like "1st slot" or "5th slot", that slot time OVERRIDES the normal course schedule.

5. **Match course codes** — Emails often mention course names or abbreviations:
   - "BCRM" or "Business Continuity" → INCS616
   - "SE" or "Software Engineering" → INCS617
   - "Cybersecurity" or "Cyber" → INCS614
   - "Big Data" or "NoSQL" → INCS615
   - "BI" or "Business Intelligence" → BINF602
   - "IT PM" or "Project Management" → BINF601
   - "Business Law" or "BTGV" → BUAD524
   - "Business Simulation" or "BizSim" → BUAD705

6. **NOT actionable** includes: general announcements with no new task, newsletters, social/spam, IT system notifications, welcome messages, workshop promotion/registration, uploaded-material notices, reference files, and purely informational emails with nothing new the student must DO or ATTEND.

7. **Extract reminders aggressively** — Add reminder items whenever the student needs to remember something for that date, including things to bring, install, download, submit, or prepare.
   - If an email says to download software for the next lab, create a reminder to download/install it.
   - If an email implies the student will need a laptop, create a separate reminder item like "Bring laptop".
   - Treat indirect preparation as actionable. Example: "Download Atlas and MongoDB for next lab" implies reminders for software setup and bringing a laptop on the lab date.

8. **Reminder dates** — Reminder items should usually use the same date as the main event unless the email gives a different prep date. If the reminder is for class/lab prep and no time is given, prefer all-day, except "bring" reminders can use the class/lab time slot.
"""


def _build_schedule_context(courses: list[dict], today: str) -> str:
    """Build a schedule string showing next class dates for each course."""
    from datetime import datetime, timedelta

    today_dt = datetime.strptime(today, "%Y-%m-%d")
    day_names = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    today_weekday = today_dt.strftime("%A")

    # Semester context — weeks are Sunday-based
    # Week 1 (Feb 8): Lectures only
    # Week 2 (Feb 15): Lab/tutorial week 1
    # Week 3 (Feb 22): Lab/tutorial week 2
    # Week 4 (Mar 1): Lab/tutorial week 3
    # Week 5 (Mar 8): Lab/tutorial week 4
    semester_start = datetime(2026, 2, 8)  # Sunday of week 1
    days_since = (today_dt - semester_start).days
    current_week = max(1, (days_since // 7) + 1)
    # Week 1 is lecture week (no labs/tutorials), so lab/tutorial week = semester_week - 1
    lab_week = max(0, current_week - 1)

    lines = [
        f"Current semester week: {current_week} of 12",
        f"Current lab/tutorial week: {lab_week} (week 1 was lectures only, no labs/tutorials)",
        f"Today is: {today_weekday}, {today}",
        "",
        "University slot map:",
        *[
            f"  Slot {slot}: {start}-{end}"
            for slot, (start, end) in sorted(config.SLOT_TIMES.items())
        ],
        "",
        "Course schedule — use NEAREST date unless email explicitly says otherwise:",
    ]

    for course in courses:
        for slot in course.get("schedule", []):
            day = slot["day"]
            start = slot.get("start", "")
            end = slot.get("end", "")
            slot_type = slot.get("type", "")

            # Find next occurrence of this day
            target_day_idx = day_names.index(day)
            today_idx = day_names.index(today_weekday)
            days_ahead = (target_day_idx - today_idx) % 7
            if days_ahead == 0:
                days_ahead = 7  # next week if today
            next_date = today_dt + timedelta(days=days_ahead)
            next_date_str = next_date.strftime("%Y-%m-%d")
            next_week_date = next_date + timedelta(days=7)
            next_week_date_str = next_week_date.strftime("%Y-%m-%d")

            lines.append(
                f"  {course['code']} ({course['name']}) — "
                f"{day} {start}-{end} ({slot_type}) — "
                f"NEAREST: {next_date_str}, FOLLOWING: {next_week_date_str}"
            )

    return "\n".join(lines)


def _find_course(course_code: str, courses: list[dict]) -> dict | None:
    """Return the matching course dict for a course code."""
    for course in courses:
        if course["code"].upper() == course_code.upper():
            return course
    return None


def _nearest_session_for_course(
    course_code: str,
    courses: list[dict],
    today: str,
) -> tuple[datetime | None, dict | None]:
    """Return the nearest upcoming session date and slot for a course."""
    course = _find_course(course_code, courses)
    if not course or not course.get("schedule"):
        return None, None

    from datetime import timedelta

    today_dt = datetime.strptime(today, "%Y-%m-%d")
    day_names = [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    ]
    today_weekday = today_dt.strftime("%A")

    nearest_date = None
    nearest_slot = None
    for slot in course["schedule"]:
        target_day_idx = day_names.index(slot["day"])
        today_idx = day_names.index(today_weekday)
        days_ahead = (target_day_idx - today_idx) % 7
        if days_ahead == 0:
            days_ahead = 7
        candidate = today_dt + timedelta(days=days_ahead)
        if nearest_date is None or candidate < nearest_date:
            nearest_date = candidate
            nearest_slot = slot

    return nearest_date, nearest_slot


def _normalize_reminders(
    reminders: list[dict] | None,
    event: ClassifiedEvent,
    courses: list[dict],
    today: str,
) -> list[dict]:
    """Fill missing reminder fields and align them to the event/session."""
    nearest_date = None
    nearest_slot = None
    if event.course_code:
        nearest_date, nearest_slot = _nearest_session_for_course(
            event.course_code, courses, today
        )

    normalized = []
    for reminder in reminders or []:
        title = str(reminder.get("title", "")).strip()
        if not title:
            continue

        category = str(reminder.get("category", "other")).strip().lower() or "other"
        date = str(reminder.get("date", "")).strip() or event.date
        if not date and nearest_date:
            date = nearest_date.strftime("%Y-%m-%d")

        time_start = str(reminder.get("time_start", "")).strip()
        time_end = str(reminder.get("time_end", "")).strip()
        if category == "bring" and nearest_slot and not time_start:
            time_start = nearest_slot.get("start", "")
            time_end = nearest_slot.get("end", "")

        normalized.append(
            {
                "title": title,
                "category": category,
                "date": date,
                "time_start": time_start,
                "time_end": time_end,
                "description": str(reminder.get("description", "")).strip(),
            }
        )

    return normalized


def classify_email(
    subject: str,
    body: str,
    sender: str,
    courses: list[dict],
    today: str | None = None,
    email_sent_date: str | None = None,
) -> ClassifiedEvent:
    """
    Classify an email using OpenAI, falling back to keyword matching.

    Args:
        subject: Email subject line
        body: Email body text (preview or stripped HTML)
        sender: Sender name/email
        courses: List of course dicts from courses.json
        today: Override for today's date (YYYY-MM-DD), defaults to now
        email_sent_date: The date the email was sent (YYYY-MM-DD). Used to
            correctly anchor relative phrases like "next week" or "tomorrow".
            Defaults to today if not provided — but will be inaccurate for
            relative date phrases in older emails.

    Returns:
        ClassifiedEvent with extraction results
    """
    if today is None:
        today = datetime.now().strftime("%Y-%m-%d")

    # If no sent date provided, fall back to today — but log a warning
    # because relative date phrases will be ambiguous.
    if email_sent_date is None:
        email_sent_date = today
        logger.warning(
            "email_sent_date not provided — relative phrases like 'next week' "
            "will be anchored to today, which may be incorrect."
        )

    # Build rich context for the LLM
    course_list = ", ".join(f"{c['code']} ({c['name']})" for c in courses)
    schedule_context = _build_schedule_context(courses, today)

    user_message = (
        f"Today's date (processing date): {today}\n"
        f"Email sent date: {email_sent_date}  ← USE THIS to anchor 'next week', 'tomorrow', 'this week'\n"
        f"Known courses: {course_list}\n\n"
        f"{schedule_context}\n\n"
        f"--- EMAIL ---\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n\n"
        f"{body[:1500]}\n"
        f"--- END EMAIL ---"
    )

    # Try AI classification
    if config.OPENAI_API_KEY:
        try:
            result = _classify_with_openai(user_message)
            if result.is_actionable and result.course_code:
                result = _clamp_to_nearest_session(
                    result, courses, today, subject, body
                )
            elif result.is_actionable:
                result.reminders = _normalize_reminders(
                    result.reminders, result, courses, today
                )
            if result.is_actionable:
                _apply_relative_date_overrides(
                    result, subject, body, email_sent_date, courses
                )
                _apply_big_data_evaluation_overrides(
                    result, subject, body, email_sent_date
                )
                _apply_explicit_time_overrides(result, subject, body)
                result.reminders = _normalize_reminders(
                    result.reminders, result, courses, today
                )
                _apply_slot_time_overrides(result, subject, body)
            return result
        except Exception as e:
            logger.warning(f"OpenAI classification failed, using fallback: {e}")

    # Fallback to keyword matching
    fallback = _classify_with_keywords(subject, body, courses)
    if fallback.is_actionable and fallback.course_code:
        fallback = _clamp_to_nearest_session(
            fallback, courses, today, subject, body
        )
    elif fallback.is_actionable:
        fallback.reminders = _normalize_reminders(
            fallback.reminders, fallback, courses, today
        )
    if fallback.is_actionable:
        _apply_relative_date_overrides(
            fallback, subject, body, email_sent_date, courses
        )
        _apply_big_data_evaluation_overrides(
            fallback, subject, body, email_sent_date
        )
        _apply_explicit_time_overrides(fallback, subject, body)
        fallback.reminders = _normalize_reminders(
            fallback.reminders, fallback, courses, today
        )
        _apply_slot_time_overrides(fallback, subject, body)
    return fallback


def _clamp_to_nearest_session(
    event: ClassifiedEvent,
    courses: list[dict],
    today: str,
    subject: str,
    body: str,
) -> ClassifiedEvent:
    """
    Post-process: clamp the AI's date to the nearest upcoming class
    session for that course. Also auto-fill times from schedule.
    """
    preserve_model_date = _has_explicit_timing_context(subject, body)
    nearest_date, nearest_slot = _nearest_session_for_course(
        event.course_code, courses, today
    )
    if not nearest_date:
        return event

    nearest_str = nearest_date.strftime("%Y-%m-%d")
    if not preserve_model_date and event.date != nearest_str:
        logger.info(
            f"   📅 Date adjusted: {event.date} → {nearest_str} "
            f"(using nearest class session)"
        )
        event.date = nearest_str

    # Auto-fill times from schedule if the AI left them empty,
    # EXCEPT for deadlines which should be all-day events (empty times).
    if event.event_type.lower() == "deadline" or ("submission" in event.title.lower()):
        event.time_start = ""
        event.time_end = ""
    elif nearest_slot and not event.time_start:
        event.time_start = nearest_slot.get("start", "")
        event.time_end = nearest_slot.get("end", "")

    event.reminders = _normalize_reminders(event.reminders, event, courses, today)
    return event


def _classify_with_openai(user_message: str) -> ClassifiedEvent:
    """Send email to OpenAI for structured classification."""
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.1,
        max_tokens=500,
        response_format={"type": "json_object"},
    )

    raw = response.choices[0].message.content or ""
    data = json.loads(raw)
    logger.debug(f"OpenAI classification: {json.dumps(data, indent=2)}")

    return ClassifiedEvent(
        is_actionable=data.get("is_actionable", False),
        event_type=data.get("event_type", ""),
        course_code=data.get("course_code", ""),
        title=data.get("title", ""),
        date=data.get("date", ""),
        time_start=data.get("time_start", ""),
        time_end=data.get("time_end", ""),
        description=data.get("description", ""),
        study_topics=data.get("study_topics", []),
        materials_needed=data.get("materials_needed", []),
        reminders=data.get("reminders", []),
        confidence=data.get("confidence", 0.0),
    )


# ── Keyword Fallback ────────────────────────────────────────────────

_KEYWORDS = {
    "exam": ["exam", "midterm", "final", "test"],
    "quiz": ["quiz", "pop quiz"],
    "assignment": ["assignment", "homework", "hw", "submit", "submission"],
    "deadline": ["deadline", "due date", "due on", "due by"],
    "project": ["project", "milestone", "deliverable"],
    "lab": ["lab report", "lab session", "practical"],
}

_DATE_PATTERN = re.compile(
    r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})"
    r"|(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})"
)
_MONTH_NAME_DATE_PATTERN = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)
_TIME_PATTERN = re.compile(
    r"\b(\d{1,2}:\d{2})\s*([AP]M)\b",
    re.IGNORECASE,
)
_WEEKDAY_PATTERN = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_SLOT_PATTERN = re.compile(r"\b([1-9])(?:st|nd|rd|th)\s+slot\b", re.IGNORECASE)


def _has_explicit_timing_context(subject: str, body: str) -> bool:
    """Return True if the email itself already gives a concrete or relative date."""
    text = f"{subject}\n{body}"
    lower = text.lower()
    relative_markers = [
        "today",
        "tomorrow",
        "this week",
        "next week",
        "end of this week",
        "by the end of this week",
    ]
    return bool(
        _DATE_PATTERN.search(text)
        or _MONTH_NAME_DATE_PATTERN.search(text)
        or _WEEKDAY_PATTERN.search(text)
        or _SLOT_PATTERN.search(text)
        or any(marker in lower for marker in relative_markers)
    )


def _find_course_session_in_week(
    course_code: str,
    courses: list[dict],
    week_start: datetime,
    week_end: datetime,
) -> tuple[str, dict] | tuple[None, None]:
    """Find the first course session that falls within a specific week window."""
    course = _find_course(course_code, courses)
    if not course:
        return None, None

    for day_offset in range((week_end - week_start).days + 1):
        candidate = week_start + timedelta(days=day_offset)
        weekday = candidate.strftime("%A")
        for slot in course.get("schedule", []):
            if slot.get("day") == weekday:
                return candidate.strftime("%Y-%m-%d"), slot

    return None, None


def _apply_relative_date_overrides(
    event: ClassifiedEvent,
    subject: str,
    body: str,
    email_sent_date: str,
    courses: list[dict],
) -> None:
    """Resolve common relative phrases against the email sent date."""
    text = f"{subject}\n{body}"
    lower = text.lower()
    if _DATE_PATTERN.search(text) or _MONTH_NAME_DATE_PATTERN.search(text):
        return

    anchor = datetime.strptime(email_sent_date, "%Y-%m-%d")
    resolved_relative_date = None

    if "tomorrow" in lower:
        resolved_relative_date = (anchor + timedelta(days=1)).strftime("%Y-%m-%d")
    elif re.search(r"\btoday\b", lower):
        resolved_relative_date = anchor.strftime("%Y-%m-%d")
    elif "end of this week" in lower or "by the end of this week" in lower:
        week_end = anchor + timedelta(days=(5 - anchor.weekday()) % 7)
        resolved_relative_date = week_end.strftime("%Y-%m-%d")
    elif "this week" in lower and event.course_code:
        week_start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
        week_end = week_start + timedelta(days=6)
        date_str, slot = _find_course_session_in_week(
            event.course_code, courses, week_start, week_end
        )
        if date_str:
            resolved_relative_date = date_str
            if slot and not event.time_start:
                event.time_start = slot.get("start", "")
                event.time_end = slot.get("end", "")
    elif "next week" in lower and event.course_code:
        current_week_start = anchor - timedelta(days=(anchor.weekday() + 1) % 7)
        next_week_start = current_week_start + timedelta(days=7)
        next_week_end = next_week_start + timedelta(days=6)
        date_str, slot = _find_course_session_in_week(
            event.course_code, courses, next_week_start, next_week_end
        )
        if date_str:
            resolved_relative_date = date_str
            if slot and not event.time_start:
                event.time_start = slot.get("start", "")
                event.time_end = slot.get("end", "")

    if not resolved_relative_date:
        return

    event.date = resolved_relative_date
    for reminder in event.reminders:
        if reminder.get("category") == "bring" and reminder.get("time_start"):
            reminder["date"] = resolved_relative_date
            continue
        reminder["date"] = resolved_relative_date


def _extract_slot_number(subject: str, body: str) -> int | None:
    """Return an explicitly mentioned slot number, if present."""
    text = f"{subject}\n{body}"
    match = _SLOT_PATTERN.search(text)
    if not match:
        return None
    return int(match.group(1))


def _extract_explicit_start_time(subject: str, body: str) -> str:
    """Return an explicit clock time from the email, if present."""
    text = f"{subject}\n{body}"
    match = _TIME_PATTERN.search(text)
    if not match:
        return ""
    return datetime.strptime(
        f"{match.group(1)} {match.group(2).upper()}",
        "%I:%M %p",
    ).strftime("%H:%M")


def _apply_explicit_time_overrides(
    event: ClassifiedEvent,
    subject: str,
    body: str,
) -> None:
    """Prefer explicit clock times mentioned in the email body."""
    explicit_start = _extract_explicit_start_time(subject, body)
    if not explicit_start:
        return

    event.time_start = explicit_start
    if not event.time_end:
        start_obj = datetime.strptime(explicit_start, "%H:%M")
        event.time_end = (start_obj + timedelta(minutes=90)).strftime("%H:%M")


def _apply_slot_time_overrides(
    event: ClassifiedEvent,
    subject: str,
    body: str,
) -> None:
    """Override event timing when the email references a numbered slot."""
    if _extract_explicit_start_time(subject, body):
        return

    slot_number = _extract_slot_number(subject, body)
    if slot_number is None:
        return

    slot_time = config.get_slot_time(slot_number)
    if not slot_time:
        return

    event.time_start, event.time_end = slot_time
    for reminder in event.reminders:
        if reminder.get("category") == "bring":
            reminder["time_start"], reminder["time_end"] = slot_time


def _apply_big_data_evaluation_overrides(
    event: ClassifiedEvent,
    subject: str,
    body: str,
    email_sent_date: str,
) -> None:
    """Resolve the Big Data Sunday-vs-Tuesday evaluation split."""
    if event.course_code.upper() != "INCS615":
        return

    text = f"{subject}\n{body}".lower()
    if "evaluation schedule" not in text and "evaluations for big data assignment 1" not in text:
        return
    if "thursday labs will have their evaluations on tuesday" not in text:
        return
    if "6bi2" not in subject.lower():
        return

    anchor = datetime.strptime(email_sent_date, "%Y-%m-%d")
    days_ahead = (1 - anchor.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    tuesday = anchor + timedelta(days=days_ahead)
    event.date = tuesday.strftime("%Y-%m-%d")
    event.time_start = ""
    event.time_end = ""


def _infer_keyword_reminders(subject: str, body: str) -> list[dict]:
    """Infer simple reminder items from obvious prep language."""
    text = f"{subject}\n{body}"
    lowered = text.lower()
    reminders = []

    software_matches = re.findall(
        r"(?:download|install)\s+([A-Za-z0-9+ .,\-/&]+?)(?:\s+(?:for|before|by|on)\b|[.\n,]|$)",
        text,
        flags=re.IGNORECASE,
    )
    if software_matches:
        software_list = software_matches[0].strip(" .,\n")
        category = "download" if "download" in lowered else "install"
        reminders.append(
            {
                "title": f"{category.title()} {software_list}",
                "category": category,
                "date": "",
                "time_start": "",
                "time_end": "",
                "description": (
                    f"{category.title()} or set up {software_list} before the related class activity."
                ),
            }
        )

    if (
        ("download" in lowered or "install" in lowered)
        and ("lab" in lowered or "practical" in lowered or "session" in lowered)
        and "laptop" not in lowered
    ):
        reminders.append(
            {
                "title": "Bring laptop",
                "category": "bring",
                "date": "",
                "time_start": "",
                "time_end": "",
                "description": (
                    "The email implies you will need your laptop for this class or lab."
                ),
            }
        )

    return reminders


def _classify_with_keywords(
    subject: str, body: str, courses: list[dict]
) -> ClassifiedEvent:
    """Simple keyword-based fallback classifier."""
    text = f"{subject} {body}".lower()
    reminders = _infer_keyword_reminders(subject, body)

    # Detect event type
    detected_type = ""
    for event_type, keywords in _KEYWORDS.items():
        if any(kw in text for kw in keywords):
            detected_type = event_type
            break

    if not detected_type and reminders:
        if "lab" in text or "practical" in text or "session" in text:
            detected_type = "lab"
        else:
            detected_type = "other"

    if not detected_type:
        return ClassifiedEvent(is_actionable=False)

    # Try to extract course code
    detected_course = ""
    for course in courses:
        course_terms = [
            word for word in course["name"].lower().split() if len(word) > 3
        ]
        if (
            course["code"].lower() in text
            or course["name"].lower() in text
            or any(word in text for word in course_terms)
        ):
            detected_course = course["code"]
            break

    # Try to extract date
    detected_date = ""
    match = _DATE_PATTERN.search(f"{subject} {body}")
    if match:
        groups = match.groups()
        if groups[0]:  # DD/MM/YYYY or MM/DD/YYYY
            d, m, y = groups[0], groups[1], groups[2]
            if len(y) == 2:
                y = f"20{y}"
            detected_date = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
        elif groups[3]:  # YYYY-MM-DD
            detected_date = f"{groups[3]}-{groups[4].zfill(2)}-{groups[5].zfill(2)}"

    return ClassifiedEvent(
        is_actionable=True,
        event_type=detected_type,
        course_code=detected_course,
        title=f"{detected_type.title()}: {subject[:60]}",
        date=detected_date,
        description=body[:200],
        reminders=reminders,
        confidence=0.5,
    )
