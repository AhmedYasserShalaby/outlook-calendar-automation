"""
gmail_client.py — Gmail API email fetcher.

Reads emails from Gmail using the same Google OAuth credentials
used for Calendar. Designed to work with Outlook-to-Gmail forwarding.
"""

import base64
import logging
import re
from datetime import datetime, timedelta

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

logger = logging.getLogger(__name__)

# We need both Gmail read and Calendar scopes
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar",
]


def get_credentials() -> Credentials:
    """
    Load or create Google OAuth2 credentials with Gmail + Calendar scopes.
    Shared credentials so user only signs in once.
    """
    creds = None

    try:
        creds = Credentials.from_authorized_user_file(
            config.GOOGLE_TOKEN_PATH, SCOPES
        )
    except (FileNotFoundError, ValueError):
        pass

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            config.GOOGLE_CREDENTIALS_PATH, SCOPES
        )
        creds = flow.run_local_server(port=0)

        with open(config.GOOGLE_TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        logger.info("Google credentials saved (Gmail + Calendar)")

    return creds


def _get_gmail_service():
    """Build Gmail API service."""
    creds = get_credentials()
    return build("gmail", "v1", credentials=creds)


def _strip_html(html: str) -> str:
    """Strip HTML tags from content."""
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _get_header(headers: list[dict], name: str) -> str:
    """Extract a header value from Gmail message headers."""
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _decode_body(part: dict) -> str:
    """Decode a Gmail message body part from base64url."""
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    try:
        decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        return decoded
    except Exception:
        return ""


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    mime_type = payload.get("mimeType", "")

    # Simple single-part message
    if mime_type == "text/plain":
        return _decode_body(payload)[:2000]
    elif mime_type == "text/html":
        return _strip_html(_decode_body(payload))[:2000]

    # Multipart message
    parts = payload.get("parts", [])
    plain_text = ""
    html_text = ""

    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            plain_text = _decode_body(part)
        elif part_mime == "text/html":
            html_text = _decode_body(part)
        elif part_mime.startswith("multipart/"):
            # Nested multipart — recurse
            nested = _extract_body(part)
            if nested:
                plain_text = plain_text or nested

    if plain_text:
        return plain_text[:2000]
    elif html_text:
        return _strip_html(html_text)[:2000]
    return ""


def fetch_emails(hours_back: int | None = None) -> list[dict]:
    """
    Fetch emails from Gmail received within the scan window.

    Filters for emails forwarded from the university domain.

    Args:
        hours_back: Override for how far back to scan (default from config).

    Returns:
        List of email dicts:
        {id, subject, sender, sender_email, body, received_at}
    """
    if hours_back is None:
        hours_back = config.SCAN_WINDOW_HOURS

    service = _get_gmail_service()

    # Build Gmail search query
    since = datetime.now() - timedelta(hours=hours_back)
    after_str = since.strftime("%Y/%m/%d")

    # Search for emails from the university domain (forwarded)
    query_parts = [f"after:{after_str}"]
    if config.UNIVERSITY_DOMAIN:
        query_parts.append(f"from:{config.UNIVERSITY_DOMAIN}")
    query = " ".join(query_parts)

    logger.info(f"Gmail search: {query}")

    all_emails = []
    page_token = None

    while True:
        results = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=50,
            pageToken=page_token,
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            break

        for msg_meta in messages:
            try:
                msg = service.users().messages().get(
                    userId="me",
                    id=msg_meta["id"],
                    format="full",
                ).execute()

                headers = msg.get("payload", {}).get("headers", [])
                subject = _get_header(headers, "Subject") or "(no subject)"
                from_header = _get_header(headers, "From")
                date_header = _get_header(headers, "Date")

                # Parse sender
                sender_email = ""
                sender_name = from_header
                email_match = re.search(r"<(.+?)>", from_header)
                if email_match:
                    sender_email = email_match.group(1)
                    sender_name = from_header[: email_match.start()].strip().strip('"')
                elif "@" in from_header:
                    sender_email = from_header.strip()
                    sender_name = sender_email.split("@")[0]

                body = _extract_body(msg.get("payload", {}))

                all_emails.append({
                    "id": msg_meta["id"],
                    "subject": subject,
                    "sender": sender_name or "Unknown",
                    "sender_email": sender_email,
                    "body": body,
                    "received_at": date_header,
                })
            except Exception as e:
                logger.warning(f"Failed to parse message {msg_meta['id']}: {e}")
                continue

        page_token = results.get("nextPageToken")
        if not page_token:
            break

    logger.info(f"Fetched {len(all_emails)} emails from the last {hours_back}h")
    return all_emails
