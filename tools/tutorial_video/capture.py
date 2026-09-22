"""Capture the real xoaila interface in Polish and English with Playwright."""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts" / "tutorial_videos" / "screens"
BASE_URL = "http://127.0.0.1:8010"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


async def viewport_shot(page, name, locator=None):
    await page.wait_for_timeout(900)
    path = OUTPUT / f"{name}.png"
    if locator:
        target = page.locator(locator).first
        await target.scroll_into_view_if_needed()
        await page.wait_for_timeout(400)
    await page.screenshot(path=str(path), full_page=False)
    print(path.relative_to(ROOT))


async def capture_language(page, language, organization_id=1):
    prefix = "/pl" if language == "pl" else ""
    content_language = language

    await page.goto(f"{BASE_URL}{prefix}/", wait_until="networkidle")
    await viewport_shot(page, f"{language}_01_landing")
    await viewport_shot(page, f"{language}_02_how", "#how")
    await viewport_shot(page, f"{language}_03_plans", "#plans")

    await page.goto(f"{BASE_URL}{prefix}/accounts/register/", wait_until="networkidle")
    await viewport_shot(page, f"{language}_04_register")
    await page.goto(f"{BASE_URL}{prefix}/accounts/login/", wait_until="networkidle")
    await viewport_shot(page, f"{language}_05_login")

    await page.fill("#id_username", "demo.customer")
    await page.fill("#id_password", "Xoaila-demo-2026!")
    await page.locator("form").filter(has=page.locator("#id_password")).locator("button[type=submit]").click()
    await page.wait_for_load_state("networkidle")

    await page.goto(f"{BASE_URL}{prefix}/dashboard/", wait_until="networkidle")
    await viewport_shot(page, f"{language}_06_dashboard")

    await page.goto(f"{BASE_URL}{prefix}/dashboard/organizations/{organization_id}/edit/", wait_until="networkidle")
    await viewport_shot(page, f"{language}_07_form_identity")
    await viewport_shot(page, f"{language}_08_form_languages", "#company-translations")
    editor = page.locator("#editing-language")
    await editor.select_option(content_language)
    await page.wait_for_timeout(300)
    await viewport_shot(page, f"{language}_09_form_products", f'[data-product-editor="{content_language}"]')
    await viewport_shot(page, f"{language}_10_form_faq", "[data-faq-language]:not([hidden])")

    await page.goto(f"{BASE_URL}/companies/greenwise-studio/{content_language}/", wait_until="networkidle")
    await viewport_shot(page, f"{language}_11_profile")
    await page.goto(f"{BASE_URL}/api/public/greenwise-studio/{content_language}/company.json", wait_until="networkidle")
    await viewport_shot(page, f"{language}_12_json")
    await page.goto(f"{BASE_URL}/api/public/greenwise-studio/{content_language}/company.md", wait_until="networkidle")
    await viewport_shot(page, f"{language}_13_markdown")

async def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=str(CHROME),
            headless=True,
            args=["--hide-scrollbars", "--force-color-profile=srgb"],
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            color_scheme="light",
            locale="pl-PL",
        )
        page = await context.new_page()
        languages = [sys.argv[1]] if len(sys.argv) > 1 else ["pl", "en"]
        for language in languages:
            await context.clear_cookies()
            await capture_language(page, language)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
