import asyncio
import logging
import re
from datetime import datetime, timedelta
from playwright.async_api import async_playwright
from playwright_stealth import stealth
import config

logger = logging.getLogger(__name__)


_FULL_DATE_PATTERN = re.compile(
    r"\b(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}:\d{2}\s+[AP]M)\b",
    re.IGNORECASE,
)
_VISIBLE_TIME_PATTERN = re.compile(
    r"^(?:\d{1,2}:\d{2}\s+[AP]M|(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{1,2}:\d{2}\s+[AP]M)$",
    re.IGNORECASE,
)
_ROW_DATE_LABEL_PATTERN = re.compile(
    r"^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+\d{1,2}/\d{1,2}$",
    re.IGNORECASE,
)


def _parse_row_text(row_text: str) -> tuple[str, str, str, str]:
    """Extract sender, subject, visible timestamp, and preview from a row."""
    lines = [line.strip() for line in row_text.splitlines() if line.strip()]
    sender = "Unknown"
    subject = "No Subject"
    visible_time = ""
    preview = ""

    cleaned = [
        line for line in lines
        if line not in {"", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", "", ""}
    ]
    if len(cleaned) >= 4:
        sender = cleaned[1]
        subject = cleaned[2]
        visible_time = cleaned[3]
        preview = " ".join(cleaned[4:])

    if not _VISIBLE_TIME_PATTERN.match(visible_time) and len(cleaned) >= 5:
        sender = cleaned[2]
        subject = cleaned[3]
        visible_time = cleaned[4]
        preview = " ".join(cleaned[5:])

    if _ROW_DATE_LABEL_PATTERN.match(subject) and sender.startswith("Notification System:"):
        visible_time = subject
        subject = sender
        sender = "GIU Notification System"
    elif _ROW_DATE_LABEL_PATTERN.match(subject) and sender:
        visible_time = subject
        subject = sender

    return sender, subject, visible_time, preview


def _looks_like_valid_subject(subject: str) -> bool:
    """Filter out icon-only or malformed subjects from collapsed rows."""
    stripped = subject.strip()
    if len(stripped) < 4:
        return False
    return any(ch.isalnum() for ch in stripped)


def _extract_received_datetime(reading_pane_text: str) -> datetime | None:
    """Extract sent datetime from the Outlook reading pane header."""
    match = _FULL_DATE_PATTERN.search(reading_pane_text)
    if not match:
        return None

    month, day, year, time_text = match.groups()
    return datetime.strptime(
        f"{year}-{month.zfill(2)}-{day.zfill(2)} {time_text.upper()}",
        "%Y-%m-%d %I:%M %p",
    )


def _clean_reading_pane_text(reading_pane_text: str, subject: str) -> str:
    """Strip Outlook chrome/forwarding headers and keep the actual message body."""
    lines = [line.rstrip() for line in reading_pane_text.splitlines()]

    start_idx = 0
    for idx, line in enumerate(lines):
        if "You forwarded this message on" in line:
            start_idx = idx + 1
            break
        if _FULL_DATE_PATTERN.search(line.strip()):
            start_idx = idx + 1

    cleaned = lines[start_idx:]
    while cleaned and (
        not cleaned[0].strip()
        or cleaned[0].strip() == subject.strip()
        or cleaned[0].strip() in {"Summarize", "Reply", "Forward", "Attendee panel closed"}
        or cleaned[0].strip().startswith("To:")
        or _FULL_DATE_PATTERN.search(cleaned[0].strip())
    ):
        cleaned.pop(0)

    body_lines = []
    for line in cleaned:
        stripped = line.strip()
        if stripped.startswith("------------------------------"):
            break
        if stripped in {"Reply", "Forward", "Attendee panel closed"}:
            break
        body_lines.append(line)

    return "\n".join(body_lines).strip()


async def _find_scroll_container(message_list):
    """Find the nearest scrollable container for the Outlook message list."""
    handle = await message_list.evaluate_handle(
        """(el) => {
            let node = el;
            while (node) {
                const style = window.getComputedStyle(node);
                const overflowY = style.overflowY;
                if ((overflowY === "auto" || overflowY === "scroll") &&
                    node.scrollHeight > node.clientHeight + 20) {
                    return node;
                }
                node = node.parentElement;
            }
            return el;
        }"""
    )
    return handle.as_element() or message_list


async def _scroll_forward(page, scroll_container) -> bool:
    """Advance the Outlook list and report whether scroll position changed."""
    before = await scroll_container.evaluate("(el) => el.scrollTop")
    await scroll_container.evaluate(
        "(el) => { el.scrollTop = el.scrollTop + Math.max(el.clientHeight * 0.9, 400); }"
    )
    await asyncio.sleep(1.2)
    after = await scroll_container.evaluate("(el) => el.scrollTop")

    if after == before:
        try:
            await page.keyboard.press("PageDown")
            await asyncio.sleep(1.2)
            after = await scroll_container.evaluate("(el) => el.scrollTop")
        except Exception:
            pass

    return after > before

async def fetch_emails_async(hours_back: int) -> list[dict]:
    """Async implementation of email fetching via Playwright."""
    all_emails = []
    seen_ids = set()
    cutoff_dt = datetime.now() - timedelta(hours=hours_back)
    
    async with async_playwright() as p:
        # Launch browser with stealthy settings
        context = await p.chromium.launch_persistent_context(
            user_data_dir=config.BROWSER_STATE_DIR,
            headless=True,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        
        try:
            logger.info("Navigating to Outlook...")
            await page.goto("https://outlook.office.com/mail/", wait_until="networkidle", timeout=60000)
            
            # ── Handle Login Selection Screen ────────────────────────────
            # Give a moment for redirections
            await asyncio.sleep(3)
            
            # If we see the "Pick an account" screen
            if await page.get_by_text("Pick an account").is_visible(timeout=5000):
                logger.info("Found account picker, selecting account...")
                try:
                    await page.locator('div[role="button"]').filter(has_text=config.OUTLOOK_EMAIL).first.click(timeout=5000)
                except Exception as e:
                    logger.warning(f"Failed specialized click, trying fallback: {e}")
                    await page.click(f'text={config.OUTLOOK_EMAIL}', timeout=5000)

            # If we see "Enter password"
            if await page.get_by_text("Enter password").is_visible(timeout=5000):
                logger.info("Found password screen, entering password...")
                await page.fill('input[name="passwd"]', config.OUTLOOK_PASSWORD)
                await page.click("#idSIButton9") # Sign in button
                await asyncio.sleep(5)
                await page.screenshot(path="debug_after_password.png")
            
            # If we see "Stay signed in?" (standard MS button ID idSIButton9)
            if await page.get_by_text("Stay signed in?").is_visible(timeout=5000):
                logger.info("Found 'Stay signed in' prompt, clicking Yes...")
                try:
                    await page.click("#idSIButton9", timeout=5000)
                except:
                    await page.click("input[type='submit'][value='Yes']", timeout=5000)
            
            # Wait for email list to load
            try:
                # Outlook sometimes uses different roles. Wait for an element that looks like an email container.
                await page.wait_for_selector('div[aria-label="Message list"]', timeout=30000)
                await page.screenshot(path="debug_inbox.png")
            except:
                logger.error("Email list failed to load. Are you logged in? Run login_outlook.py first.")
                await page.screenshot(path="debug_outlook.png", full_page=True)
                await context.close()
                return []

            message_list = await page.query_selector('div[aria-label="Message list"]')
            scroll_container = (
                await _find_scroll_container(message_list) if message_list else None
            )
            stagnant_rounds = 0
            crawl_round = 0
            previous_visible_signatures: tuple[str, ...] = ()

            while stagnant_rounds < 4 and crawl_round < config.CRAWL_MAX_ROUNDS:
                crawl_round += 1
                email_items = await page.query_selector_all('div[role="option"]')
                logger.info(
                    f"Found {len(email_items)} email items in the visible list (round {crawl_round})."
                )
                added_this_round = 0
                saw_older_than_cutoff = False
                visible_signatures = []

                for item in email_items:
                    try:
                        row_text = await item.inner_text()
                        sender_text, subject, visible_time, preview = _parse_row_text(row_text)
                        if not _looks_like_valid_subject(subject):
                            continue

                        provisional_id = f"{subject}|{visible_time}|{preview[:80]}"
                        visible_signatures.append(provisional_id)
                        if provisional_id in seen_ids:
                            continue

                        logger.info(f"Processing: {subject} from {sender_text}")

                        await item.click()
                        await asyncio.sleep(2)

                        body_el = await page.wait_for_selector(
                            'div[aria-label="Reading Pane"], .ReadingPaneRoot, #ReadingPaneContainerId',
                            timeout=10000,
                        )
                        body_text = await body_el.inner_text() if body_el else ""
                        received_dt = _extract_received_datetime(body_text)
                        received_iso = (
                            received_dt.strftime("%Y-%m-%d") if received_dt else ""
                        )
                        body_text = _clean_reading_pane_text(body_text, subject)[:5000]

                        if received_dt and received_dt < cutoff_dt:
                            saw_older_than_cutoff = True

                        msg_id = f"{subject}_{received_iso or visible_time}".replace(" ", "_").replace("/", "-")
                        if msg_id in seen_ids:
                            continue

                        seen_ids.add(provisional_id)
                        seen_ids.add(msg_id)

                        if received_dt and received_dt < cutoff_dt:
                            continue

                        all_emails.append({
                            "id": msg_id,
                            "subject": subject,
                            "sender": sender_text,
                            "sender_email": "",
                            "body": body_text,
                            "received_at": received_iso,
                            "preview": preview,
                        })
                        added_this_round += 1
                    except Exception as e:
                        logger.debug(f"Failed to parse an email item: {e}")
                        continue

                if not message_list:
                    break

                visible_signature_tuple = tuple(visible_signatures)
                scrolled = False
                if scroll_container:
                    scrolled = await _scroll_forward(page, scroll_container)

                if (
                    added_this_round == 0
                    and visible_signature_tuple == previous_visible_signatures
                    and not scrolled
                ):
                    stagnant_rounds += 1
                elif added_this_round == 0 and visible_signature_tuple == previous_visible_signatures:
                    stagnant_rounds += 1
                else:
                    stagnant_rounds = 0

                previous_visible_signatures = visible_signature_tuple

                if saw_older_than_cutoff:
                    break

        except Exception as e:
            logger.error(f"Scraper error: {e}")
        finally:
            await context.close()
            
    return all_emails

def fetch_emails(hours_back: int | None = None) -> list[dict]:
    """Sync wrapper for the async fetcher."""
    if hours_back is None:
        hours_back = config.SCAN_WINDOW_HOURS
    return asyncio.run(fetch_emails_async(hours_back))
