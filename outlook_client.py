"""
outlook_client.py — Outlook IMAP email fetcher.

Reads emails from Outlook directly using an App Password, bypassing Graph API
and auto-forwarding restrictions.
"""

import email
import email.policy
import imaplib
import logging
import re
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)


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


def fetch_emails(hours_back: int | None = None) -> list[dict]:
    """
    Fetch emails from Outlook received within the scan window.
    
    Args:
        hours_back: Override for how far back to scan (default from config).

    Returns:
        List of email dicts:
        {id, subject, sender, sender_email, body, received_at}
    """
    if hours_back is None:
        hours_back = config.SCAN_WINDOW_HOURS

    if not config.OUTLOOK_EMAIL or not config.OUTLOOK_PASSWORD:
        logger.error("OUTLOOK_EMAIL or OUTLOOK_PASSWORD not set in .env")
        return []

    try:
        mail = imaplib.IMAP4_SSL("outlook.office365.com")
        mail.login(config.OUTLOOK_EMAIL, config.OUTLOOK_PASSWORD)
        mail.select("inbox")

        # Build IMAP search query
        since_date = (datetime.now() - timedelta(hours=hours_back)).strftime("%d-%b-%Y")
        
        status, messages = mail.search(None, f'(SINCE "{since_date}")')
        
        all_emails = []
        if status == "OK" and messages[0]:
            msg_ids = messages[0].split()
            for msg_id in msg_ids[-50:]:  # Limit to 50 most recent to avoid massive loops
                status, msg_data = mail.fetch(msg_id, "(RFC822)")
                if status != "OK":
                    continue
                    
                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email, policy=email.policy.default)
                
                subject = msg.get("Subject", "(no subject)")
                from_header = str(msg.get("From", ""))
                date_header = str(msg.get("Date", ""))
                
                # Parse sender
                sender_email = ""
                sender_name = from_header
                email_match = re.search(r"<(.+?)>", from_header)
                if email_match:
                    sender_email = email_match.group(1)
                    sender_name = from_header[:email_match.start()].strip().strip('"')
                elif "@" in from_header:
                    sender_email = from_header.strip()
                    sender_name = sender_email.split("@")[0]
                    
                # Skip emails not from university domain if specified
                if config.UNIVERSITY_DOMAIN and not sender_email.lower().endswith(config.UNIVERSITY_DOMAIN.lower()):
                    continue

                body = ""
                # Extract body
                if msg.is_multipart():
                    for part in msg.walk():
                        content_type = part.get_content_type()
                        content_disposition = str(part.get("Content-Disposition"))

                        if "attachment" in content_disposition:
                            continue

                        if content_type == "text/plain":
                            try:
                                body = part.get_content()
                                break # Prefer plain text
                            except Exception:
                                pass
                        elif content_type == "text/html":
                            try:
                                body = _strip_html(part.get_content())
                            except Exception:
                                pass
                else:
                    content_type = msg.get_content_type()
                    try:
                        content = msg.get_content()
                        if content_type == "text/html":
                            body = _strip_html(content)
                        else:
                            body = content
                    except Exception:
                        body = ""

                message_id_header = msg.get("Message-ID", "")
                msg_id_str = str(message_id_header).strip("<>") if message_id_header else str(msg_id.decode())

                all_emails.append({
                    "id": msg_id_str,
                    "subject": subject,
                    "sender": sender_name or "Unknown",
                    "sender_email": sender_email,
                    "body": body[:2000],
                    "received_at": date_header,
                })

        mail.logout()
        logger.info(f"Fetched {len(all_emails)} emails from the last {hours_back}h via IMAP")
        return all_emails

    except Exception as e:
        logger.error(f"IMAP Error: {e}")
        return []
