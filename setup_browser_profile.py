"""One-time setup: launch Chromium headed for manual ChatGPT login.
Creates a persistent profile at ./chatgpt_browser_profile/ that stores
all __Secure- cookies natively. After this runs once, the headless daemon
can reuse the profile with no login prompts.
"""
import asyncio
from playwright.async_api import async_playwright

PROFILE_DIR = "./chatgpt_browser_profile"

async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            channel="chrome",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            viewport={"width": 1280, "height": 800},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        print("Opening ChatGPT. Please log in in the browser window...")
        await page.goto("https://chatgpt.com", wait_until="domcontentloaded")

        input("\nPress Enter AFTER you have logged into ChatGPT successfully...")

        # Verify we have the session cookie
        cookies = await context.cookies(urls=["https://chatgpt.com"])
        has_session = any("session-token" in c["name"] for c in cookies)
        print(f"\nSession cookie present: {has_session}")
        if has_session:
            for c in cookies:
                if "session" in c["name"]:
                    print(f"  {c['name']}: {'set' if c.get('value') else 'empty'}")
            print("\nProfile saved to", PROFILE_DIR)
            print("You can now use the headless daemon.")
        else:
            print("WARNING: No session cookie found. Login may not have completed.")

        await context.close()

if __name__ == "__main__":
    asyncio.run(main())
