"""
review_week.py — One-shot email review for the current and upcoming week.

Fetches all emails from the last 7 days via the Outlook Playwright scraper,
sends each one to GPT for classification, then prints a priority-ranked
human-readable summary with no calendar writes.

Usage:
    python review_week.py
"""

import asyncio
import json
import logging
import sys
from datetime import datetime

from openai import OpenAI

import config
import classifier as classifier_module
import outlook_scraper

logging.basicConfig(
    level=logging.WARNING,  # suppress noisy scraper logs
    format="%(asctime)s [%(levelname)s] %(message)s",
)

# ── Importance scoring ──────────────────────────────────────────────
PRIORITY_ORDER = [
    "exam",
    "quiz",
    "assignment",
    "project",
    "deadline",
    "lab",
    "makeup",
    "presentation",
    "meeting",
    "other",
]

REVIEW_SYSTEM_PROMPT = """You are an academic email classifier for a university student.
Your job is to decide if an email contains something the student must act on THIS week or NEXT week.

Respond with a valid JSON object and nothing else:
{
  "is_actionable": true/false,
  "priority": "high" | "medium" | "low",
  "event_type": "exam" | "quiz" | "assignment" | "deadline" | "project" | "lab" | "makeup" | "presentation" | "meeting" | "announcement" | "other",
  "course_code": "e.g. INCS617 — or empty if not course-specific",
  "title": "very short title (≤8 words)",
  "due_date": "YYYY-MM-DD or empty if unknown",
  "due_time": "HH:MM 24h or empty",
  "action_needed": "1-2 sentence plain-English description of exactly what the student must do or prepare",
  "confidence": 0.0 to 1.0
}

Marking guidance:
  high   — exam, quiz, graded submission, attendance required, make-up session
  medium — assignment/project deadline, lab report due
  low    — informational but still relevant (room change, schedule note)
  not actionable — spam, generic announcements, welcome emails, IT notifications with no student action
"""


def classify_for_review(email: dict, courses: list[dict], today: str) -> dict | None:
    """Classify one email with OpenAI. Returns a dict or None if not actionable."""
    if not config.OPENAI_API_KEY:
        logging.error("OPENAI_API_KEY is not set — cannot classify emails.")
        sys.exit(1)

    course_list = ", ".join(f"{c['code']} ({c['name']})" for c in courses)
    schedule_context = classifier_module._build_schedule_context(courses, today)
    email_sent_date = email.get("received_at") or today

    user_msg = (
        f"Today (processing date): {today}\n"
        f"Email sent date: {email_sent_date}  ← USE THIS to anchor 'next week', 'tomorrow', 'this week'\n"
        f"Known courses: {course_list}\n\n"
        f"{schedule_context}\n\n"
        f"--- EMAIL ---\n"
        f"From: {email['sender']}\n"
        f"Subject: {email['subject']}\n"
        f"Received: {email.get('received_at', 'unknown')}\n\n"
        f"{email['body'][:2000]}\n"
        f"--- END EMAIL ---"
    )

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
        max_tokens=350,
        response_format={"type": "json_object"},
    )
    data = json.loads(response.choices[0].message.content or "")
    if not data.get("is_actionable", False):
        return None
    data["_email_subject"] = email["subject"]
    data["_sender"] = email["sender"]
    data["_received"] = email.get("received_at", "")
    return data


def priority_rank(item: dict) -> int:
    p = item.get("priority", "low")
    rank = {"high": 0, "medium": 1, "low": 2}.get(p, 3)
    type_rank = (
        PRIORITY_ORDER.index(item.get("event_type", "other"))
        if item.get("event_type", "other") in PRIORITY_ORDER
        else 99
    )
    return rank * 100 + type_rank


def print_report(results: list[dict], today: str) -> None:
    sep = "─" * 62

    print(f"\n{'═' * 62}")
    print(f"  WEEKLY EMAIL REVIEW  |  Today: {today}")
    print(f"{'═' * 62}")

    if not results:
        print("  No actionable emails found in the last 7 days.")
        print(f"{'═' * 62}\n")
        return

    priority_labels = {"high": "🔴 HIGH", "medium": "🟡 MEDIUM", "low": "🟢 LOW"}

    for idx, item in enumerate(results, 1):
        p_label = priority_labels.get(item.get("priority", "low"), "⚪ LOW")
        etype = item.get("event_type", "").upper()
        course = item.get("course_code", "")
        title = item.get("title", item["_email_subject"])
        due = item.get("due_date", "")
        due_t = item.get("due_time", "")
        action = item.get("action_needed", "")

        due_str = f"{due}  {due_t}".strip() if due else "date TBD"

        print(f"\n  [{idx}]  {p_label}  |  {etype}  |  {course}")
        print(f"  {sep}")
        print(f"  Title   : {title}")
        print(f"  Due     : {due_str}")
        print(f"  From    : {item['_sender']}")
        print(f"  Subject : {item['_email_subject']}")
        print(f"  Action  : {action}")

    print(f"\n{'═' * 62}")
    print(f"  Total actionable items: {len(results)}")
    high = sum(1 for r in results if r.get("priority") == "high")
    medium = sum(1 for r in results if r.get("priority") == "medium")
    low = sum(1 for r in results if r.get("priority") == "low")
    print(f"  🔴 High: {high}   🟡 Medium: {medium}   🟢 Low: {low}")
    print(f"{'═' * 62}\n")


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    hours_back = 7 * 24  # one full week

    print(
        f"Fetching emails from the last 7 days (since "
        f"{datetime.now().strftime('%Y-%m-%d')} - 7 days) …"
    )
    print("This may take 30-60 seconds while the browser loads Outlook.\n")

    emails = outlook_scraper.fetch_emails(hours_back)

    if not emails:
        print("No emails were returned by the scraper.")
        print(
            "Make sure your browser session is still valid. "
            "If it expired, run: python login_outlook.py"
        )
        sys.exit(0)

    print(f"Fetched {len(emails)} emails. Classifying with AI …\n")

    courses = config.load_courses()
    results = []
    for i, email in enumerate(emails, 1):
        print(f"  [{i}/{len(emails)}] {email['subject'][:60]}", end="", flush=True)
        classified = classify_for_review(email, courses, today)
        if classified:
            print(
                f"  → {classified.get('priority', '?').upper()} / {classified.get('event_type', '?')}"
            )
            results.append(classified)
        else:
            print("  → not actionable")

    # Sort by priority then event type
    results.sort(key=priority_rank)

    print_report(results, today)


if __name__ == "__main__":
    main()
