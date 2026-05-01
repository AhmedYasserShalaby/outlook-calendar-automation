# Outlook Calendar Automation

AI-assisted workflow that scans university Outlook emails and creates structured Google Calendar events for exams, quizzes, assignments, and deadlines.

## Why It Exists

University deadlines often arrive as unstructured email text. This project turns those messages into reliable calendar events with classification, duplicate protection, audit logs, and a dry-run mode so events can be reviewed before they are created.

## Highlights

- Incremental Outlook scanning with overlap protection
- AI classification for exams, assignments, quizzes, and deadline emails
- Keyword fallback when no AI key is available
- Course-aware event mapping using `courses.json`
- SQLite state tracking to avoid repeated processing
- Google Calendar duplicate checks before event creation
- JSON audit logs for every scan and classification decision
- Dry-run mode for safe testing

## Tech Stack

- Python
- Google Calendar API
- Microsoft Outlook scraping/client workflow
- OpenAI API
- SQLite
- JSON audit logs

## Architecture

```text
Outlook email
  -> scanner / client
  -> classifier
  -> event normalization
  -> SQLite deduplication
  -> Google Calendar creation
  -> audit logs
```

## Project Structure

```text
main.py              # Orchestrates scanning, classification, and calendar writes
classifier.py        # Extracts structured event data from email text
calendar_client.py   # Google Calendar integration
outlook_scraper.py   # Outlook web scanning workflow
state.py             # SQLite processed-email tracking
config.py            # Environment and course configuration
courses.json         # Safe example course schedule
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` with local credentials and API keys.

Create your private course schedule:

```bash
cp courses.json courses.private.json
```

Then set:

```bash
COURSES_PATH=courses.private.json
```

## Usage

Dry run:

```bash
python main.py --dry-run --verbose
```

Live run:

```bash
python main.py --verbose
```

Override scan window:

```bash
python main.py --since 48 --dry-run --verbose
```

## Configuration

Required local files are intentionally ignored by Git:

- `.env`
- `credentials.json`
- `token.json`
- `processed_emails.db`
- `courses.private.json`
- `raw_email_logs/`
- `audit_logs/`

## Public Repo Safety

This repository includes only example configuration. OAuth credentials, tokens, private schedules, local databases, raw email logs, and audit logs are ignored.

## Portfolio Note

This project demonstrates practical data extraction, API integration, stateful automation, and defensive workflow design.
