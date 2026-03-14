"""
setup_auth.py — Interactive authentication setup.

Run this once to sign in to Google (Gmail + Calendar) via OAuth2.
After running, your token is cached for automated cron runs.

Usage:
    python setup_auth.py
"""

import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def setup_google():
    """Authenticate with Google for both Gmail reading and Calendar writing."""
    import config
    from pathlib import Path

    logger.info("\n" + "=" * 60)
    logger.info("  GOOGLE SETUP (Gmail + Calendar)")
    logger.info("=" * 60)

    creds_path = Path(config.GOOGLE_CREDENTIALS_PATH)
    if not creds_path.exists():
        logger.error(
            f"\n❌ Google credentials file not found at:\n   {creds_path}\n"
            "\nTo set it up:\n"
            "1. Go to https://console.cloud.google.com\n"
            "2. Create a new project (or select existing)\n"
            "3. Go to: APIs & Services → Library\n"
            "4. Enable BOTH 'Gmail API' and 'Google Calendar API'\n"
            "5. Go to: APIs & Services → Credentials\n"
            "6. Click 'Create Credentials' → OAuth client ID\n"
            "7. Application type: 'Desktop app'\n"
            "8. Click Create → Download JSON\n"
            f"9. Place the file in: {creds_path.parent}/\n"
        )
        sys.exit(1)

    logger.info("\n✅ Google credentials found. Starting OAuth flow...\n")
    logger.info("   A browser window will open for Google sign-in.")
    logger.info("   Grant access to BOTH Gmail and Calendar.\n")

    try:
        import gmail_client
        import calendar_client

        # This triggers the OAuth flow with both scopes
        creds = gmail_client.get_credentials()

        # Test Gmail
        from googleapiclient.discovery import build
        gmail = build("gmail", "v1", credentials=creds)
        profile = gmail.users().getProfile(userId="me").execute()
        logger.info(f"\n✅ Gmail access successful!")
        logger.info(f"   Email: {profile.get('emailAddress', '?')}")
        logger.info(f"   Total messages: {profile.get('messagesTotal', '?')}")

        # Test Calendar
        cal = build("calendar", "v3", credentials=creds)
        calendars = cal.calendarList().list().execute()
        logger.info(f"\n✅ Calendar access successful!")
        logger.info(f"   Your calendars:")
        for c in calendars.get("items", [])[:5]:
            primary = " (primary)" if c.get("primary") else ""
            logger.info(f"     • {c['summary']}{primary}")

        logger.info(f"\n   Token saved at: {config.GOOGLE_TOKEN_PATH}")

    except Exception as e:
        logger.error(f"\n❌ Authentication failed: {e}")
        sys.exit(1)

    logger.info("\n" + "=" * 60)
    logger.info("  🎉 SETUP COMPLETE!")
    logger.info("=" * 60)
    logger.info("\nBefore running, make sure you:")
    logger.info("  1. Set up email forwarding from Outlook to this Gmail")
    logger.info("     → Go to outlook.office365.com → Settings → Forwarding")
    logger.info("  2. Edit courses.json with your actual course schedule")
    logger.info("  3. Set OPENAI_API_KEY in .env")
    logger.info("\nThen test with:")
    logger.info("  python main.py --dry-run --verbose")


if __name__ == "__main__":
    setup_google()
