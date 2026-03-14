"""
main.py — Orchestrator for the Outlook → Google Calendar scanner.

Entry point that ties together email fetching, AI classification,
and calendar event creation. Designed to run as a daily cron job.

Usage:
    python main.py                  # normal run
    python main.py --dry-run        # classify only, no calendar writes
    python main.py --dry-run --no-audit
    python main.py --since 48       # scan last 48 hours
    python main.py --verbose        # detailed logging
"""

import argparse
import json
import logging
from pathlib import Path
import re
import sys
from datetime import datetime, timedelta

import config
import outlook_scraper
import classifier
import calendar_client
import state

logger = logging.getLogger("outlook-scanner")


def _resolve_hours_back(explicit_hours_back: int | None) -> int:
    """Start from the last scan time, with a small overlap for safety."""
    if explicit_hours_back is not None:
        return explicit_hours_back

    last_scan_time = state.get_last_scan_time()
    if not last_scan_time:
        return config.SCAN_WINDOW_HOURS

    try:
        last_scan_dt = datetime.fromisoformat(last_scan_time)
    except ValueError:
        logger.warning(
            "Could not parse last scan time '%s'; falling back to %s hours",
            last_scan_time,
            config.SCAN_WINDOW_HOURS,
        )
        return config.SCAN_WINDOW_HOURS

    elapsed = datetime.now() - last_scan_dt
    overlap = timedelta(hours=config.RESCAN_OVERLAP_HOURS)
    hours_back = max(1, int(((elapsed + overlap).total_seconds() // 3600) + 1))
    logger.info(
        "⏱️  Last scan was %s; using %s-hour lookback (%s-hour overlap)",
        last_scan_time,
        hours_back,
        config.RESCAN_OVERLAP_HOURS,
    )
    return hours_back


def _build_reminder_title(reminder: dict) -> str:
    """Format reminder titles consistently for calendar display."""
    category = str(reminder.get("category", "other")).strip().lower()
    label = category.replace("_", " ").title()
    title = str(reminder.get("title", "")).strip()
    return f"Reminder ({label}): {title}" if title else f"Reminder ({label})"


def _write_audit_log(audit_entries: list[dict], started_at: str) -> Path:
    """Persist a full scan audit log to disk for manual review."""
    audit_dir = Path(config.AUDIT_LOG_DIR)
    audit_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    audit_path = audit_dir / f"audit_{timestamp}.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "scan_started_at": started_at,
        "entry_count": len(audit_entries),
        "entries": audit_entries,
    }
    audit_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return audit_path


def _write_raw_email_log(emails: list[dict], started_at: str) -> Path:
    """Persist the raw fetched emails before classification/merging."""
    raw_dir = Path(config.RAW_EMAIL_LOG_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = raw_dir / f"raw_fetch_{timestamp}.json"
    payload = {
        "generated_at": datetime.now().isoformat(),
        "scan_started_at": started_at,
        "email_count": len(emails),
        "emails": emails,
    }
    raw_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return raw_path


def _normalize_items(items: list[str]) -> list[str]:
    """Deduplicate and normalize text list items while preserving order."""
    normalized = []
    seen = set()
    for item in items:
        value = str(item).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(value)
    return normalized


def _sentence_tokens(text: str) -> set[str]:
    """Tokenize a sentence for rough similarity checks."""
    return {
        token for token in re.findall(r"[a-z0-9]+", text.lower())
        if len(token) > 2
    }


def _merge_description_text(*parts: str) -> str:
    """Merge overlapping description lines and keep the strongest wording."""
    candidates = []
    for part in parts:
        for line in str(part).splitlines():
            value = line.strip()
            if value:
                candidates.append(value)

    merged: list[str] = []
    for candidate in candidates:
        candidate_tokens = _sentence_tokens(candidate)
        replaced = False
        for idx, existing in enumerate(merged):
            if (
                candidate.lower().startswith("attend the compensation")
                and existing.lower().startswith("attend the compensation")
            ):
                richer = candidate if len(candidate) >= len(existing) else existing
                other = existing if richer == candidate else candidate
                location_line = None
                for line in [other, richer]:
                    if "location:" in line.lower():
                        location_line = line[line.lower().index("location:"):].strip()
                        break
                if location_line and "location:" not in richer.lower():
                    richer = f"{richer.rstrip('.')}.\n{location_line}"
                merged[idx] = richer
                replaced = True
                break

            existing_tokens = _sentence_tokens(existing)
            if not candidate_tokens or not existing_tokens:
                continue
            overlap = len(candidate_tokens & existing_tokens)
            smaller = min(len(candidate_tokens), len(existing_tokens))
            if smaller and overlap / smaller >= 0.6:
                if len(candidate) > len(existing):
                    merged[idx] = candidate
                replaced = True
                break
        if not replaced:
            merged.append(candidate)

    return "\n".join(merged)


def _dedupe_reminders(reminders: list[dict]) -> list[dict]:
    """Deduplicate reminders by category, title, and date."""
    deduped = []
    seen = set()
    for reminder in reminders:
        key = (
            str(reminder.get("category", "")).lower().strip(),
            str(reminder.get("title", "")).lower().strip(),
            str(reminder.get("date", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(reminder)
    return deduped


_EXPLICIT_ACTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bquiz\b",
        r"\bexam\b",
        r"\bmidterm\b",
        r"\bfinal\b",
        r"\bpresentation\b",
        r"\bdeadline\b",
        r"\bdue\b",
        r"\bsubmission\b",
        r"\bsubmit\b",
        r"\bmake[\s-]?up\b",
        r"\bcompensation\b",
        r"\bcancel(?:led|lation)?\b",
        r"\brescheduled?\b",
        r"\bmust attend\b",
        r"\brequired\b",
        r"\bwill be held\b",
        r"\bwill take place\b",
        r"\bslot\b",
    ]
]
_SUPPORTING_MARKERS = [
    "reference",
    "guidelines",
    "available on cms",
    "uploaded",
    "added to the project document",
    "next session",
    "upcoming lab",
    "upcoming lab session",
    "attachment",
    "attachments",
    "sheet",
    "location",
    "room",
    "materials",
    "files",
    "clarification",
    "clarifications",
    "updates",
    "installation",
]
_NOISE_MARKERS = [
    "register now",
    "registration",
    "reserve your spot",
    "workshop",
    "connects",
    "newsletter",
    "transaction has been processed",
]
_NUMBERED_FAMILY_PATTERNS = [
    ("assignment", re.compile(r"\bassignment\s+(\d+)\b", re.IGNORECASE)),
    ("quiz", re.compile(r"\bquiz\s+(\d+)\b", re.IGNORECASE)),
    ("phase", re.compile(r"\bphase\s+(\d+)\b", re.IGNORECASE)),
    ("lab", re.compile(r"\blab\s+(\d+)\b", re.IGNORECASE)),
    ("tutorial", re.compile(r"\btutorial\s+(\d+)\b", re.IGNORECASE)),
    ("session", re.compile(r"\bsession\s+(\d+)\b", re.IGNORECASE)),
]


def _slugify(value: str) -> str:
    """Build a stable slug from a free-form string."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _event_family(subject: str, result: classifier.ClassifiedEvent) -> str:
    """Build a stable family key for related emails about the same event."""
    text = f"{subject} {result.title}".lower()
    if "team formation" in text:
        return "team-formation"
    if re.search(r"\bicas?\b", text) or "in-class activit" in text:
        return "ica-submission"
    if "compensation" in text:
        return "compensation"
    if "rescheduled" in text:
        return "rescheduled-session"
    if "cancel" in text:
        return "cancelled-session"
    if "git" in text or "github" in text:
        return "git-github"

    for label, pattern in _NUMBERED_FAMILY_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        suffix = match.group(1)
        if label == "phase" and "presentation" in text:
            return f"phase-{suffix}-presentation"
        if label == "phase" and "project" in text:
            return f"phase-{suffix}-project"
        return f"{label}-{suffix}"

    if "project" in text:
        return "project"
    if result.event_type:
        return _slugify(result.event_type)
    return _slugify(result.title) or "other"


def _extract_locations(*texts: str) -> list[str]:
    """Extract simple room/location references from email text."""
    locations = []
    pattern = re.compile(r"\b[A-Z]\d\.\d{3}\b|\broom\s+[A-Z0-9.\-]+\b", re.IGNORECASE)
    for text in texts:
        for match in pattern.findall(text or ""):
            value = match.strip()
            if value.lower().startswith("room "):
                value = value[5:]
            locations.append(value)
    return _normalize_items(locations)


def _title_quality_score(result: classifier.ClassifiedEvent) -> tuple[int, int]:
    """Rank titles so richer merged events keep the more useful wording."""
    title = result.title.lower()
    bonus = 0
    if "tutorial" in title or "lab" in title or "assignment" in title:
        bonus += 3
    if "location" in title or "meeting" in title:
        bonus -= 1
    return (bonus, len(result.title.strip()))


def _timing_quality_score(email: dict, result: classifier.ClassifiedEvent) -> tuple[int, int]:
    """Rank timing confidence so explicit slot/time emails override generic ones."""
    text = f"{email.get('subject', '')} {email.get('body', '')}".lower()
    score = 0
    if "slot" in text:
        score += 3
    if re.search(r"\b\d{1,2}:\d{2}\b", text):
        score += 2
    if result.time_start and result.time_start not in {"", "00:00"}:
        score += 1
    return (score, len(text))


def _build_canonical_event_key(
    subject: str,
    body: str,
    result: classifier.ClassifiedEvent,
) -> str:
    """Build a stable key for dedupe across runs and calendar sync."""
    family = _event_family(subject, result)
    course = result.course_code.strip().upper() or "UNKNOWN"
    date = result.date.strip() or "undated"
    return f"{course}|{family}|{date}"


def _classify_event_role(
    result: classifier.ClassifiedEvent,
    subject: str,
    body: str,
) -> str:
    """Distinguish primary calendar items from supporting context emails."""
    text = f"{subject}\n{body}".lower()
    if result.event_type.lower() in {"quiz", "exam", "presentation", "makeup"}:
        return "primary_event"
    if any(pattern.search(text) for pattern in _EXPLICIT_ACTION_PATTERNS):
        return "primary_event"
    if any(marker in text for marker in _SUPPORTING_MARKERS):
        return "supporting_email"
    return "supporting_email"


def _event_is_past(result: classifier.ClassifiedEvent, now_dt: datetime) -> bool:
    """Return True when the resolved event has already passed."""
    if not result.date:
        return False
    try:
        event_date = datetime.strptime(result.date, "%Y-%m-%d").date()
    except ValueError:
        return False

    today = now_dt.date()
    if event_date < today:
        return True
    if event_date > today:
        return False

    check_time = result.time_end or result.time_start
    if not check_time:
        return False
    try:
        event_dt = datetime.strptime(
            f"{result.date} {check_time}", "%Y-%m-%d %H:%M"
        )
    except ValueError:
        return False
    return event_dt < now_dt


def _evaluate_event_eligibility(
    email: dict,
    result: classifier.ClassifiedEvent,
    now_dt: datetime,
) -> tuple[bool, str]:
    """Apply deterministic gating to reduce stale and noisy events."""
    if not result.is_actionable:
        return False, "classifier_not_actionable"

    text = f"{email.get('subject', '')}\n{email.get('body', '')}".lower()
    if any(marker in text for marker in _NOISE_MARKERS):
        return False, "noise_marketing_or_registration"
    if "uploaded" in text or "added to the project document" in text:
        return False, "supporting_material_update"
    if (
        ("next session" in text or "upcoming lab" in text or "upcoming lab session" in text)
        and "quiz" not in text
        and "exam" not in text
        and "deadline" not in text
        and "submission" not in text
        and "presentation" not in text
    ):
        return False, "prep_for_future_session_only"
    if _event_is_past(result, now_dt):
        return False, "past_event_date"
    if not result.course_code.strip():
        return False, "missing_course_code"

    role = _classify_event_role(result, email.get("subject", ""), email.get("body", ""))
    if role != "primary_event":
        return True, "supporting_email"
    return True, "eligible_primary"


def _mark_result_rejected(
    result: classifier.ClassifiedEvent, reason: str
) -> classifier.ClassifiedEvent:
    """Convert a candidate into a non-actionable result with a reason."""
    result.is_actionable = False
    result.eligibility_reason = reason
    return result


def _merge_classifications(
    classified_emails: list[dict],
) -> list[dict]:
    """Merge related emails into stronger consolidated events."""
    grouped: dict[str, dict] = {}

    for item in classified_emails:
        email = item["email"]
        result = item["result"]
        if not result.is_actionable:
            item["merged"] = False
            item["merge_key"] = None
            continue

        merge_key = (
            result.canonical_event_key
            or _build_canonical_event_key(email["subject"], email["body"], result)
        )
        item["merge_key"] = merge_key

        existing = grouped.get(merge_key)
        if not existing:
            grouped[merge_key] = {
                "result": classifier.ClassifiedEvent(**result.to_dict()),
                "source_emails": [email],
                "locations": _extract_locations(email["subject"], email["body"], result.description),
                "primary_email": email,
            }
            item["merged"] = False
            continue

        item["merged"] = True
        merged = existing["result"]
        existing["source_emails"].append(email)
        existing["locations"] = _normalize_items(
            existing["locations"] + _extract_locations(email["subject"], email["body"], result.description)
        )

        if _timing_quality_score(email, result) > _timing_quality_score(
            existing["primary_email"], merged
        ):
            merged.time_start = result.time_start
            merged.time_end = result.time_end
            existing["primary_email"] = email
        elif (not merged.time_start or merged.time_start in {"00:00", ""}) and result.time_start:
            merged.time_start = result.time_start
            merged.time_end = result.time_end
        elif result.time_start and result.time_end and not merged.time_end:
            merged.time_end = result.time_end

        if _title_quality_score(result) > _title_quality_score(merged):
            merged.title = result.title
            merged.event_type = result.event_type

        merged.study_topics = _normalize_items(merged.study_topics + result.study_topics)
        merged.materials_needed = _normalize_items(merged.materials_needed + result.materials_needed)
        merged.reminders = _dedupe_reminders(merged.reminders + result.reminders)
        merged.confidence = max(merged.confidence, result.confidence)

        merged.description = _merge_description_text(
            merged.description.strip(),
            result.description.strip(),
        )

    consolidated = []
    for merge_key, payload in grouped.items():
        merged_result = payload["result"]
        locations = payload["locations"]
        for reminder in merged_result.reminders:
            if reminder.get("category") == "bring" and merged_result.time_start:
                reminder["time_start"] = merged_result.time_start
                reminder["time_end"] = merged_result.time_end

        if locations:
            merged_result.materials_needed = _normalize_items(
                merged_result.materials_needed + [f"Location: {loc}" for loc in locations]
            )
            location_line = f"Location: {', '.join(locations)}."
            if location_line not in merged_result.description:
                merged_result.description = _merge_description_text(
                    merged_result.description,
                    location_line,
                )

        consolidated.append(
            {
                "merge_key": merge_key,
                "result": merged_result,
                "source_emails": payload["source_emails"],
                "locations": locations,
            }
        )

    return consolidated


def _build_study_metadata(result: classifier.ClassifiedEvent) -> dict:
    """Build a consistent metadata payload for downstream study automation."""
    return {
        "study_topics": _normalize_items(result.study_topics),
        "materials_needed": _normalize_items(result.materials_needed),
        "canonical_event_key": result.canonical_event_key,
    }


def _build_event_description(consolidated: dict) -> str:
    """Build a clearer event description for calendar and study automation."""
    result = consolidated["result"]
    metadata = _build_study_metadata(result)
    source_lines = [
        f"- {email['received_at'] or 'unknown date'} | {email['subject']}"
        for email in consolidated["source_emails"]
    ]
    study_lines = [
        f"- {topic}" for topic in metadata["study_topics"]
    ] or ["- Review the source email details."]
    material_lines = [
        f"- {item}" for item in metadata["materials_needed"]
    ] or ["- No extra materials were explicitly listed."]
    reminder_lines = [
        (
            f"- [{str(reminder.get('category', 'other')).strip().title()}] "
            f"{str(reminder.get('title', '')).strip()} "
            f"({reminder.get('date', result.date)}"
            f"{' ' + reminder.get('time_start', '') if reminder.get('time_start') else ''})"
        ).rstrip()
        for reminder in result.reminders
    ] or ["- No extra reminder items."]

    return (
        f"📋 WHAT TO DO:\n{result.description}\n\n"
        f"📚 WHAT TO STUDY THIS WEEK/DAY:\n" + "\n".join(study_lines) + "\n\n"
        f"🧰 MATERIALS / THINGS TO PREPARE:\n" + "\n".join(material_lines) + "\n\n"
        f"⏰ REMINDERS / SUBTASKS:\n" + "\n".join(reminder_lines) + "\n\n"
        f"📧 RELATED EMAILS:\n" + "\n".join(source_lines)
    )


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the scanner."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")


def run(
    dry_run: bool = False,
    hours_back: int | None = None,
    verbose: bool = False,
    audit: bool = True,
) -> None:
    """
    Main scanner pipeline.

    1. Load courses
    2. Fetch new emails from Gmail (forwarded from Outlook)
    3. Filter already-processed emails
    4. Classify each email with AI
    5. Create Google Calendar events for actionable items
    6. Log results
    """
    setup_logging(verbose)

    # Resolve settings
    started_at = datetime.now().isoformat()
    audit_entries: list[dict] = []
    hours_back = _resolve_hours_back(hours_back)
    if dry_run or config.DRY_RUN:
        dry_run = True
        logger.info("🔍 DRY RUN MODE — no calendar events will be created")

    # ── Step 1: Load courses ─────────────────────────────────────────
    try:
        courses = config.load_courses()
        logger.info(
            f"📚 Loaded {len(courses)} courses: "
            + ", ".join(c["code"] for c in courses)
        )
    except FileNotFoundError as e:
        logger.error(str(e))
        sys.exit(1)

    # ── Step 2: Fetch emails from Outlook ────────────────────────────
    logger.info(f"📧 Fetching emails from the last {hours_back} hours...")
    try:
        emails = outlook_scraper.fetch_emails(hours_back)
    except Exception as e:
        logger.error(f"Failed to fetch emails: {e}")
        sys.exit(1)

    if not emails:
        logger.info("📭 No new emails found")
        state.log_scan(0, 0)
        return

    logger.info(f"📬 Found {len(emails)} emails")
    raw_log_path = _write_raw_email_log(emails, started_at)
    logger.info(f"🗂️  Raw email log: {raw_log_path}")

    # ── Step 3: Filter already-processed ─────────────────────────────
    if dry_run:
        new_emails = emails
    else:
        new_emails = [e for e in emails if not state.is_processed(e["id"])]
    skipped = len(emails) - len(new_emails)
    if skipped:
        logger.info(f"⏭️  Skipped {skipped} already-processed emails")

    if not new_emails:
        logger.info("✅ All emails already processed")
        state.log_scan(len(emails), 0)
        return

    # ── Step 4: Classify ─────────────────────────────────────────────
    events_created = 0
    now_dt = datetime.now()
    today = now_dt.strftime("%Y-%m-%d")
    classified_emails: list[dict] = []

    for email in new_emails:
        logger.info(f"\n{'─' * 50}")
        logger.info(f"📩 Subject: {email['subject']}")
        logger.info(f"   From:    {email['sender']} <{email['sender_email']}>")

        # Classify
        result = classifier.classify_email(
            subject=email["subject"],
            body=email["body"],
            sender=f"{email['sender']} <{email['sender_email']}>",
            courses=courses,
            today=today,
            email_sent_date=email.get("received_at") or today,
        )

        result.canonical_event_key = _build_canonical_event_key(
            email["subject"], email["body"], result
        )
        result.classification_role = _classify_event_role(
            result, email["subject"], email["body"]
        )
        is_eligible, eligibility_reason = _evaluate_event_eligibility(
            email, result, now_dt
        )
        result.eligibility_reason = eligibility_reason
        if not is_eligible:
            _mark_result_rejected(result, eligibility_reason)

        if result.is_actionable:
            logger.info(
                f"   🎯 {result.event_type.upper()} | {result.course_code} | "
                f"{result.title} | {result.date} {result.time_start or '(all day)'}"
            )
            logger.info(f"   📝 {result.description[:100]}")
            logger.info(
                f"   🧠 role={result.classification_role} | key={result.canonical_event_key}"
            )
        else:
            logger.info(
                f"   ⚪ Not actionable ({result.eligibility_reason or 'rejected'})"
            )

        classified_emails.append(
            {
                "email": email,
                "result": result,
            }
        )

    primary_keys = {
        item["result"].canonical_event_key
        for item in classified_emails
        if item["result"].is_actionable
        and item["result"].classification_role == "primary_event"
    }

    for item in classified_emails:
        result = item["result"]
        if not result.is_actionable or result.classification_role != "supporting_email":
            continue
        if result.canonical_event_key in primary_keys:
            result.eligibility_reason = "supporting_email_merged_with_primary"
            continue
        if (
            result.canonical_event_key
            and result.date
            and not dry_run
            and calendar_client.has_event_with_canonical_key(
                result.canonical_event_key, result.date
            )
        ):
            _mark_result_rejected(result, "supporting_email_existing_calendar_event")
            continue
        _mark_result_rejected(result, "supporting_email_without_primary")

    # ── Step 5: Consolidate related emails into stronger events ─────
    consolidated_events = _merge_classifications(classified_emails)

    # ── Step 6: Create events and write audit rows ───────────────────
    for consolidated in consolidated_events:
        result = consolidated["result"]
        source_emails = consolidated["source_emails"]
        event_id = None
        description = _build_event_description(consolidated)
        study_metadata = _build_study_metadata(result)
        study_metadata_block = ""
        if study_metadata["study_topics"] or study_metadata["materials_needed"]:
            study_metadata_block = (
                f"\n\n🤖 BOT_METADATA_START\n"
                f"{json.dumps(study_metadata, indent=2)}\n"
                f"🤖 BOT_METADATA_END"
            )

        if result.is_actionable:
            logger.info(f"\n{'═' * 50}")
            logger.info(
                f"🧩 Consolidated | {result.course_code} | {result.title} | "
                f"{result.date} {result.time_start or '(all day)'} | "
                f"sources={len(source_emails)} | key={result.canonical_event_key}"
            )

            if not dry_run:
                event_id = calendar_client.create_event(
                    title=result.title,
                    date=result.date,
                    time_start=result.time_start,
                    time_end=result.time_end,
                    description=f"{description}{study_metadata_block}",
                    course_code=result.course_code,
                    courses=courses,
                    canonical_event_key=result.canonical_event_key,
                )
                if event_id:
                    events_created += 1
            else:
                logger.info("   🏜️  [DRY RUN] Would create consolidated calendar event")
                events_created += 1

        for item in classified_emails:
            email = item["email"]
            result_obj = item["result"]
            if item.get("merge_key") != consolidated["merge_key"]:
                continue
            calendar_action = (
                "created"
                if event_id
                else "dry_run_create"
                if dry_run
                else "merged_or_skipped_create"
                if item.get("merged", False)
                else "not_created"
            )
            audit_entries.append(
                {
                    "email_id": email["id"],
                    "received_at": email.get("received_at", ""),
                    "sender": email["sender"],
                    "subject": email["subject"],
                    "preview": email.get("preview", ""),
                    "cleaned_body": email["body"],
                    "classification": result_obj.to_dict(),
                    "consolidated_classification": result.to_dict(),
                    "source_email_ids": [src["id"] for src in source_emails],
                    "merged_into_related_event": item.get("merged", False),
                    "canonical_event_key": result.canonical_event_key,
                    "classification_role": result_obj.classification_role,
                    "eligibility_reason": result_obj.eligibility_reason,
                    "calendar_action": calendar_action,
                    "calendar_event_id": event_id,
                    "reminder_event_ids": [],
                    "was_processed": not dry_run,
                    "calendar_write_attempted": not dry_run,
                }
            )
            if not dry_run:
                state.mark_processed(
                    email["id"],
                    email["subject"],
                    event_id,
                    event_date=result.date,
                    canonical_event_key=result.canonical_event_key,
                    calendar_action=calendar_action,
                    eligibility_reason=result_obj.eligibility_reason,
                    classification_role=result_obj.classification_role,
                )

    for item in classified_emails:
        if item["result"].is_actionable:
            continue
        email = item["email"]
        audit_entries.append(
            {
                "email_id": email["id"],
                "received_at": email.get("received_at", ""),
                "sender": email["sender"],
                "subject": email["subject"],
                "preview": email.get("preview", ""),
                "cleaned_body": email["body"],
                "classification": item["result"].to_dict(),
                "consolidated_classification": None,
                "source_email_ids": [email["id"]],
                "merged_into_related_event": False,
                "canonical_event_key": item["result"].canonical_event_key,
                "classification_role": item["result"].classification_role,
                "eligibility_reason": item["result"].eligibility_reason,
                "calendar_action": "rejected",
                "calendar_event_id": None,
                "reminder_event_ids": [],
                "was_processed": not dry_run,
                "calendar_write_attempted": False,
            }
        )
        if not dry_run:
            state.mark_processed(
                email["id"],
                email["subject"],
                event_date=item["result"].date,
                canonical_event_key=item["result"].canonical_event_key,
                calendar_action="rejected",
                eligibility_reason=item["result"].eligibility_reason,
                classification_role=item["result"].classification_role,
            )

    # ── Step 7: Summary ──────────────────────────────────────────────
    logger.info(f"\n{'═' * 50}")
    logger.info(f"📊 Scan complete:")
    logger.info(f"   Emails scanned:  {len(emails)}")
    logger.info(f"   New emails:      {len(new_emails)}")
    logger.info(f"   Events created:  {events_created}")
    logger.info(f"   Raw email log:   {raw_log_path}")
    if audit:
        audit_path = _write_audit_log(audit_entries, started_at)
        logger.info(f"   Audit log:       {audit_path}")
    logger.info(f"{'═' * 50}")

    state.log_scan(len(emails), events_created)


def main():
    parser = argparse.ArgumentParser(
        description="Scan Outlook emails and create Google Calendar events"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify emails but don't create calendar events",
    )
    parser.add_argument(
        "--since",
        type=int,
        default=None,
        help="Hours back to scan (overrides SCAN_WINDOW_HOURS)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug logging",
    )
    parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Skip writing the JSON audit log for this run",
    )
    args = parser.parse_args()

    run(
        dry_run=args.dry_run,
        hours_back=args.since,
        verbose=args.verbose,
        audit=not args.no_audit,
    )


if __name__ == "__main__":
    main()
