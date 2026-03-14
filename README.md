# 📧 Outlook → Google Calendar Scanner

Automatically scans your college Outlook inbox daily and creates Google Calendar events for exams, assignments, quizzes, and other important deadlines.

## ✨ Features

- **Incremental scanning** — resumes from the last successful scan with a small overlap buffer
- **Stateful deduplication** — skips emails already recorded in `processed_emails.db`
- **AI classification** — uses OpenAI GPT to understand email context
- **Course-aware** — maps events to your courses with color-coded calendar entries
- **Calendar duplicate protection** — avoids recreating near-duplicate calendar events on overlapping runs
- **Past/noise filtering** — rejects stale items, workshop noise, CMS-upload notices, and prep-only emails that should not become calendar events
- **Keyword fallback** — works even without an AI API key
- **Dry-run mode** — test without creating real calendar events
- **Audit logs** — saves a JSON file per scan with the cleaned email text and extracted events

---

## 🚀 Quick Start

### 1. Install dependencies

```bash
cd /Users/ahmedyasser/Documents/NotUseless/Outlook
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your API credentials (see sections below)
```

### 3. Set up your courses

Edit `courses.json` with your actual course schedule:

```json
{
  "courses": [
    {
      "code": "CSEN301",
      "name": "Computer Architecture",
      "color": "7",
      "schedule": [
        { "day": "Sunday", "start": "11:00", "end": "12:30" }
      ]
    }
  ]
}
```

> **Color IDs**: 1=Lavender, 2=Sage, 3=Grape, 4=Flamingo, 5=Banana, 6=Tangerine, 7=Peacock, 8=Graphite, 9=Blueberry, 10=Basil, 11=Tomato

### 4. Set up authentication

```bash
python setup_auth.py
```

This will walk you through both Outlook and Google Calendar auth flows.

### 5. Test it

```bash
# Dry run — see what would be created
python main.py --dry-run --verbose

# Real run
python main.py --verbose

# Dry run without writing an audit file
python main.py --dry-run --no-audit
```

Normal runs automatically resume from the last successful scan stored in `processed_emails.db`/`scan_log`. The scanner also adds a small overlap buffer so boundary emails are not missed, then relies on SQLite state plus Google Calendar duplicate checks to avoid re-adding old items.

Important behavior:

- Prep emails like `upcoming lab`, `next session`, `install X before the lab`, or `Phase uploaded on CMS` should usually enrich an existing event or be rejected, not become a new standalone calendar event.
- Explicit times mentioned in the email, like `11:05 AM`, should win over default schedule slots.
- Older emails fetched during a catch-up run should not be rolled forward into the current week unless the email explicitly says the event is for the current/upcoming week.
- Group-specific schedule splits in an email must be respected. Example: if an email says Sunday groups evaluate on Sunday and Thursday groups evaluate on Tuesday, the workflow must choose the branch that matches the student's actual group.

### 6. Schedule it

Current production deployment runs on Ubuntu via cron twice daily at midnight and noon Egypt time:

```bash
0 0,12 * * * cd /home/ubuntu/Outlook && /home/ubuntu/Outlook/venv/bin/python main.py >> /home/ubuntu/Outlook/cron_log.txt 2>&1
```

If you still want a local macOS scheduler, you can use `launchd`:

```bash
# Copy plist to LaunchAgents
cp com.outlook-scanner.plist ~/Library/LaunchAgents/

# Load it (starts immediately + runs daily at 7 AM)
launchctl load ~/Library/LaunchAgents/com.outlook-scanner.plist

# Check logs
tail -f ~/Library/Logs/outlook-scanner.log
```

To **stop** the cron:

```bash
launchctl unload ~/Library/LaunchAgents/com.outlook-scanner.plist
```

---

## 🔑 Microsoft Azure Setup

1. Go to [Azure Portal](https://portal.azure.com) → **App registrations** → **New registration**
2. Name: `Outlook Calendar Scanner`
3. Account type: **Accounts in this organizational directory only**
4. Redirect URI: Select **Mobile and desktop applications** → check the `https://login.microsoftonline.com/common/oauth2/nativeclient` option
5. After registration, copy:
   - **Application (client) ID** → `MS_CLIENT_ID` in `.env`
   - **Directory (tenant) ID** → `MS_TENANT_ID` in `.env`
6. Go to **API permissions** → **Add permission** → **Microsoft Graph** → **Delegated** → `Mail.Read` → **Grant admin consent**

## 🔑 Google Calendar Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com) → Create project
2. **APIs & Services** → **Library** → Search "Google Calendar API" → **Enable**
3. **APIs & Services** → **Credentials** → **Create Credentials** → **OAuth client ID**
4. Application type: **Desktop app** → Create → **Download JSON**
5. Rename to `credentials.json` and place in this project directory

## 🔑 OpenAI Setup

1. Go to [OpenAI Platform](https://platform.openai.com/api-keys) → Create API key
2. Paste as `OPENAI_API_KEY` in `.env`
3. Model `gpt-4o-mini` is used by default (cheapest + fast)

---

## 📁 Project Structure

```
Outlook/
├── main.py              # Entry point — orchestrator
├── config.py            # Loads .env + courses.json
├── outlook_scraper.py   # Playwright Outlook web scraper
├── login_outlook.py     # One-time interactive Outlook login helper
├── classifier.py        # AI email → event extractor
├── gmail_client.py      # Shared Google auth / legacy Gmail fetcher
├── calendar_client.py   # Google Calendar event creator
├── state.py             # SQLite dedup tracker
├── setup_auth.py        # One-time auth setup helper
├── courses.json         # Your course schedule
├── raw_email_logs/      # Raw Outlook fetch snapshots
├── audit_logs/          # Post-classification audit logs
├── .env                 # API keys (gitignored)
├── .env.example         # Template for .env
├── requirements.txt     # Python dependencies
└── com.outlook-scanner.plist  # macOS launchd cron config
```

## 🛠️ CLI Options

```
python main.py [OPTIONS]

  --dry-run       Classify emails but don't create calendar events
  --no-audit      Skip writing the JSON audit log for this run
  --since HOURS   Override the automatic incremental lookback window
  --verbose, -v   Enable debug logging
```

## 🔎 Audit Logs

Every run writes a JSON audit file to `audit_logs/` by default.
Every fetch also writes the raw pre-classification Outlook payload to `raw_email_logs/`.

Each entry includes:
- Raw email identifiers and sent date
- Sender, subject, preview, and cleaned body text
- Structured classifier output
- Consolidated event data and calendar event IDs
- Eligibility decisions such as rejected stale/noisy/prep-only emails
- Canonical event keys used for cross-run dedupe

This makes it easy to review exactly what the system saw before trusting the calendar output.
