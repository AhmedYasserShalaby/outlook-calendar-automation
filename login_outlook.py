import asyncio
from playwright.async_api import async_playwright
import config
from pathlib import Path

async def run():
    print("Opening browser for Outlook login...")
    state_dir = Path(config.BROWSER_STATE_DIR)
    state_dir.parent.mkdir(parents=True, exist_ok=True)
    
    async with async_playwright() as p:
        # Launch browser with stealthy settings
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(state_dir),
            headless=False,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={'width': 1280, 'height': 800},
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        page = context.pages[0] if context.pages else await context.new_page()
        print("Navigating to Outlook...")
        await page.goto("https://outlook.office.com/mail/")
        
        print("\n" + "="*50)
        print("ACTION REQUIRED:")
        print("1. Log in with your REAL university password (NOT the App Password).")
        print("2. Complete your 2FA approval.")
        print("3. IMPORTANT: Check 'Stay signed in' when asked.")
        print("4. Once you see your Inbox, hit ENTER in this terminal.")
        print("="*50 + "\n")
        
        try:
            input("Wait for your Inbox to load, then press ENTER here... (Do NOT use Ctrl+C)")
        except KeyboardInterrupt:
            print("\nDetected Ctrl+C, but still attempting to save session...")
        
        print("Saving browser session... Please wait.")
        # Close pages first to ensure state is flushed
        for page in context.pages:
            await page.close()
        await context.close()
        print("✅ Session saved! You can now run 'python main.py'")

if __name__ == "__main__":
    asyncio.run(run())
