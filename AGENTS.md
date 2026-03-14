# Outlook to Google Calendar Automation

This repository automates the extraction of academic events from college email (Outlook forwarded to Gmail) and creates highly structured Google Calendar events. It serves as the "always-on" sentinel that feeds downstream studying automation.

## Core Workflow

1.  **Direct Outlook Fetching**: `outlook_scraper.py` uses a headless Playwright browser to log into the Outlook web interface directly. This bypasses university IT blocks on Graph API and IMAP.
2.  **Gmail Fetching (Secondary)**: `gmail_client.py` can still be used for accounts where forwarding is enabled.
3.  **State Management**: `state.py` checks `processed_emails.db` to ensure we don't process the same email twice.
    - Normal workflow runs must resume from the last successful scan recorded in `scan_log`, not from a naive fixed window.
    - The scanner should intentionally include a small overlap buffer when resuming so we do not miss edge-case emails that arrived around the last run boundary. `processed_emails.db` and the calendar duplicate check are the safety net for that overlap.
    - When the user says `start the workflow`, interpret that as: fetch emails after the last handled scan, keep the overlap buffer, skip anything already processed, and only create calendar events for genuinely new items.
4.  **Raw Fetch Logging**: Every Outlook fetch writes a raw JSON snapshot to `raw_email_logs/` before classification so we can audit exactly what the scraper extracted from Outlook.
5.  **AI Classification**: `classifier.py` sends the cleaned email body and the current `courses.json` schedule to OpenAI (GPT-4o mini). The AI determines if the email contains an actionable event (Quiz, Exam, Deadline, Lab Prep, etc.) and extracts dates, times, study topics, materials, and reminder subtasks.
6.  **Event Consolidation**: `main.py` merges related emails from the same sender/course/date window into one stronger event. For example, a compensation-location email and a compensation-details email should become one consolidated calendar event with the stronger time plus the room/location.
7.  **Calendar Event Creation**: `calendar_client.py` creates one main Google Calendar event per consolidated item. Reminder/preparation tasks are embedded inside that event's description instead of being created as separate calendar events. It also injects machine-readable JSON metadata into the event description for downstream consumption.

## File Architecture

| File | Purpose |
| :--- | :--- |
| `main.py` | The main orchestrator. Handles fallback between Outlook and Gmail. |
| `outlook_scraper.py` | **(NEW)** Headless browser scraper with stealth support to bypass API blocks. |
| `login_outlook.py` | **(NEW)** Interactive tool to perform the first-time login and save the browser session. |
| `classifier.py` | OpenAI prompt engineering for event extraction and date inference. |
| `gmail_client.py` | Handles Gmail API OAuth and fetching (Legacy/Secondary). |
| `calendar_client.py` | Handles Google Calendar API, duplicate prevention, and course-coded colors. |
| `state.py` | SQLite wrapper (`processed_emails.db`) to track processed message IDs. |
| `raw_email_logs/` | Raw Outlook fetch snapshots for auditing what the scraper actually saw before classification. |
| `audit_logs/` | Post-classification audit logs including raw email, extracted event, and consolidated event data. |
| `setup_auth.py` | Local script for Google OAuth token generation. |
| `courses.json` | Schedule configuration (Course codes, names, colors, and times). |
| `config.py` | Configuration and environment variable loader. |

## Current Calendar Behavior

-   The system now prefers **one calendar event per consolidated academic item**, not one event plus many standalone reminder events.
-   Manual and scheduled runs should both behave incrementally: start from the last successful scan in `scan_log`, add a small overlap buffer, then let `processed_emails.db` and Google Calendar duplicate checks prevent re-adding old items.
-   Prep-only emails such as "bring laptop", "install Git", "uploaded on CMS", "next session", or "upcoming lab" must **not** become standalone future calendar events unless they clearly introduce a new quiz, deadline, presentation, attendance requirement, or dated academic event.
-   Reminder/preparation details such as "bring laptop", "install Git", "prepare Zomato case study", or "print one-page sheet" are written into the main event description under clear sections.
-   Related emails from the same sender should be merged when they clearly refer to the same course event. Example: one email says there is a BUAD705 compensation tutorial on **Sunday, March 8, 2026** in **5th slot**, and another says it will be in **M3.430**. These should become one event with the stronger time (`15:45-17:15`) and the room included.
-   The event description should stay highly readable for the downstream study project. It should clearly spell out:
    - what to do
    - what to study this week/day
    - what materials or software are needed
    - which source emails were merged

## Accuracy Rules

-   When resuming a workflow, treat the **last successful scan time** as the primary boundary for fetching new mail.
-   Keep a small overlap window when resuming from the last scan so delayed rendering, ordering quirks, or near-boundary arrivals do not cause misses.
-   Treat exact dates in emails as higher priority than default schedule clamping.
-   Treat relative phrases like `tomorrow`, `this week`, and `end of this week` relative to the **email sent date**, not the processing date.
-   Treat slot language like `1st slot` through `5th slot` as authoritative timing overrides.
-   Treat an **explicit clock time** in the email body, such as `11:05 AM`, as higher priority than schedule-slot defaults.
-   Do not let old prep emails roll forward into a later week just because they mention `next session` or `upcoming lab`. Those phrases are relative to the original email context.
-   If an email explicitly splits groups or sections across different dates, such as "Sunday labs on Sunday, Thursday labs on Tuesday", prefer the branch that matches the student's actual group instead of blindly taking the first listed date.
-   If a course topic or case study has already been completed, old prep reminders for that topic should not be recreated in later runs.
-   When scraping Outlook, prefer full reading-pane extraction over row preview text whenever possible.
-   If accuracy is uncertain, inspect `raw_email_logs/` and `audit_logs/` before trusting the calendar result.

## Cloud Deployment (Current State)

This project is deployed on an **Ubuntu VPS (IP: 92.5.76.116)**.
-   **Location**: `/home/ubuntu/Outlook`
-   **Execution**: It runs via `cron` twice daily at exactly **12:00 AM (midnight)** and **12:00 PM (noon)** local Egypt time (`Africa/Cairo`).
-   **Logging**: Output is piped to `/home/ubuntu/Outlook/cron_log.txt`.

### Cron Job Installed
```bash
0 0,12 * * * cd /home/ubuntu/Outlook && /home/ubuntu/Outlook/venv/bin/python main.py >> /home/ubuntu/Outlook/cron_log.txt 2>&1
```

## Security & Secret Management

-   **WARNING**: You must **never** commit `.env`, `token.json`, or the Google `client_secret_*.json` file to version control. They are strictly ignored in `.gitignore`.
-   If migrating servers, you must manually run `python setup_auth.py` on your local machine to generate a valid `token.json`, and then securely `scp` the `.env`, `client_secret_*.json`, and `token.json` files to the production environment.

## Integration with "Study" Repo

The `Outlook` repo is designed to act exclusively as a data producer. It offloads all document downloading and study-guide generation responsibilities down the pipeline.
It does this by appending a JSON artifact to the end of every Google Calendar description:
```json
🤖 BOT_METADATA_START
{
  "study_topics": ["topic 1", "topic 2"],
  "materials_needed": ["lecture 1", "lab 2"]
}
🤖 BOT_METADATA_END
```
Whenever modifying `classifier.py`, **ensure this payload structure remains intact** so you do not break the `study_assistant.py` downloader logic.
