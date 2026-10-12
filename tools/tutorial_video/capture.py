"""Capture the real xoaila interface in Polish and English with Playwright."""

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts" / "tutorial_videos" / "screens"
BASE_URL = "http://127.0.0.1:8010"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


async def blur_prices(page):
    """Hide only monetary values; keep labels and all other demo data readable."""
    await page.evaluate(
        r"""
        () => {
            const amountPattern = /\d[\d\s.,]*\s(?:PLN|EUR)/gi;
            const walker = document.createTreeWalker(
                document.body,
                NodeFilter.SHOW_TEXT,
                {
                    acceptNode(node) {
                        const parent = node.parentElement;
                        if (!parent || parent.closest('[data-tutorial-price-mask]')) {
                            return NodeFilter.FILTER_REJECT;
                        }
                        if (['SCRIPT', 'STYLE', 'NOSCRIPT'].includes(parent.tagName)) {
                            return NodeFilter.FILTER_REJECT;
                        }
                        amountPattern.lastIndex = 0;
                        return amountPattern.test(node.nodeValue || '')
                            ? NodeFilter.FILTER_ACCEPT
                            : NodeFilter.FILTER_REJECT;
                    }
                }
            );
            const nodes = [];
            while (walker.nextNode()) nodes.push(walker.currentNode);

            for (const node of nodes) {
                const text = node.nodeValue || '';
                const fragment = document.createDocumentFragment();
                let cursor = 0;
                amountPattern.lastIndex = 0;
                for (const match of text.matchAll(amountPattern)) {
                    fragment.append(document.createTextNode(text.slice(cursor, match.index)));
                    const mask = document.createElement('span');
                    mask.dataset.tutorialPriceMask = 'true';
                    mask.textContent = match[0];
                    mask.style.display = 'inline-block';
                    mask.style.filter = 'blur(8px)';
                    mask.style.userSelect = 'none';
                    fragment.append(mask);
                    cursor = match.index + match[0].length;
                }
                fragment.append(document.createTextNode(text.slice(cursor)));
                node.replaceWith(fragment);
            }
        }
        """
    )


async def viewport_shot(page, name, locator=None):
    await page.wait_for_timeout(900)
    await blur_prices(page)
    path = OUTPUT / f"{name}.png"
    if locator:
        target = page.locator(locator).first
        await target.scroll_into_view_if_needed()
        await page.evaluate("window.scrollTo(0, window.scrollY)")
        await page.wait_for_timeout(400)
    await page.screenshot(path=str(path), full_page=False)
    print(path.relative_to(ROOT))


async def capture_language(page, language, organization_id=1):
    prefix = "/pl" if language == "pl" else ""
    content_language = language

    await page.goto(f"{BASE_URL}{prefix}/", wait_until="networkidle")
    await viewport_shot(page, f"{language}_01_landing")
    await viewport_shot(page, f"{language}_02_how", "#how")
    if language == "en":
        await page.evaluate("document.body.style.zoom = '0.9'")
    await viewport_shot(page, f"{language}_03_plans", "#plans")
    if language == "en":
        await page.evaluate("document.body.style.zoom = '1'")

    await page.goto(f"{BASE_URL}{prefix}/accounts/register/", wait_until="networkidle")
    await viewport_shot(page, f"{language}_04_register")
    await page.goto(f"{BASE_URL}{prefix}/accounts/login/", wait_until="networkidle")
    await viewport_shot(page, f"{language}_05_login")

    await page.fill("#id_username", "jan.kowalski")
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
