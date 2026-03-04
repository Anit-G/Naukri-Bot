"""Playwright-based Naukri auto-apply bot.

This module migrates the legacy Selenium flow to Playwright sync API while preserving
profile-based login reuse and adding robust waits/retries.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

import pandas as pd
from playwright.sync_api import Error, TimeoutError as PlaywrightTimeoutError, sync_playwright

T = TypeVar("T")

# ---------- User configuration ----------
FIREFOX_PROFILE_PATH = ""  # Existing Firefox profile path used for persisted login.
KEYWORDS = [""]  # Add desired role keywords.
LOCATION = ""  # e.g. "bangalore" or keep "" for all.
MAX_APPLY_COUNT = 100
CSV_FILE = "naukriapplied.csv"
HEADLESS = False
# ---------------------------------------


@dataclass
class ApplyState:
    applied: int = 0
    failed: int = 0
    passed_links: list[str] = field(default_factory=list)
    failed_links: list[str] = field(default_factory=list)


def with_retry(fn: Callable[[], T], attempts: int = 3, delay_seconds: float = 1.5) -> T:
    """Retry transient Playwright failures."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except (PlaywrightTimeoutError, Error) as exc:
            last_exc = exc
            if attempt < attempts:
                time.sleep(delay_seconds)
    assert last_exc is not None
    raise last_exc


def build_search_url(keyword: str, location: str, i: int) -> str:
    """Build URL using original template with `{i+1}` substitution."""
    slug = keyword.lower().replace(" ", "-")
    if location.strip() == "":
        return f"https://www.naukri.com/{slug}-{i + 1}"
    location_slug = location.lower().replace(" ", "-")
    return f"https://www.naukri.com/{slug}-jobs-in-{location_slug}-{i + 1}"


def collect_job_links(listing_page, keywords: list[str], location: str) -> list[str]:
    links: list[str] = []

    for keyword in keywords:
        for i in range(21):  # i = 0..20
            url = build_search_url(keyword, location, i)
            print(f"Opening listing page: {url}")
            with_retry(lambda: listing_page.goto(url, wait_until="domcontentloaded", timeout=30_000))

            anchors = listing_page.locator("div.srp-jobtuple-wrapper a.title[href]")
            with_retry(lambda: anchors.first.wait_for(state="visible", timeout=12_000))

            count = anchors.count()
            for idx in range(count):
                href = anchors.nth(idx).get_attribute("href")
                if href:
                    links.append(href)

    # Keep order while removing duplicates.
    return list(dict.fromkeys(links))


def handle_chatbot_flow(job_page, job_url: str) -> bool:
    """Handle chatbot drawer flow in a dedicated function.

    Returns True if application appears to be completed, else False.
    """
    try:
        drawer = job_page.locator("div.chatbot_DrawerContentWrapper")
        drawer.wait_for(state="visible", timeout=8_000)
    except PlaywrightTimeoutError:
        return False

    print(f"Chatbot flow detected for: {job_url}")

    # Minimal safe handling: wait briefly to allow bot auto-submit/transition.
    time.sleep(2)

    applied_text = job_page.locator("div.job-title-text", has_text="Applied to")
    if applied_text.count() > 0:
        try:
            applied_text.first.wait_for(state="visible", timeout=6_000)
            return True
        except PlaywrightTimeoutError:
            return False

    return False


def process_job_link(context, job_url: str, state: ApplyState) -> None:
    job_page = context.new_page()  # Open in new tab; listing page remains open.
    try:
        with_retry(lambda: job_page.goto(job_url, wait_until="domcontentloaded", timeout=30_000))

        apply_button = job_page.locator("#apply-button")
        with_retry(lambda: apply_button.wait_for(state="visible", timeout=12_000))
        apply_text = apply_button.inner_text().strip()

        if apply_text != "Apply":
            print(f"Skipping non-exact apply button text '{apply_text}' for: {job_url}")
            state.failed += 1
            state.failed_links.append(job_url)
            return

        with_retry(lambda: apply_button.click(timeout=8_000))

        confirmation = job_page.locator("div.job-title-text", has_text="Applied to")
        chatbot_drawer = job_page.locator("div.chatbot_DrawerContentWrapper")

        applied_successfully = False
        try:
            confirmation.first.wait_for(state="visible", timeout=6_000)
            applied_successfully = True
        except PlaywrightTimeoutError:
            try:
                chatbot_drawer.first.wait_for(state="visible", timeout=4_000)
                applied_successfully = handle_chatbot_flow(job_page, job_url)
            except PlaywrightTimeoutError:
                applied_successfully = False

        if applied_successfully:
            state.applied += 1
            state.passed_links.append(job_url)
            print(f"Applied successfully: {job_url} | Count: {state.applied}")
        else:
            state.failed += 1
            state.failed_links.append(job_url)
            print(f"Apply flow incomplete: {job_url}")

    except Exception as exc:  # noqa: BLE001 - log and continue next job.
        state.failed += 1
        state.failed_links.append(job_url)
        print(f"Failed for {job_url}: {exc}")
    finally:
        job_page.close()


def save_results(state: ApplyState) -> None:
    final_dict = {
        "passed": pd.Series(state.passed_links),
        "failed": pd.Series(state.failed_links),
    }
    pd.DataFrame.from_dict(final_dict).to_csv(CSV_FILE, index=False)


def run() -> None:
    if not FIREFOX_PROFILE_PATH:
        raise ValueError("Set FIREFOX_PROFILE_PATH to your existing Firefox profile path.")

    state = ApplyState()

    with sync_playwright() as playwright:
        context = playwright.firefox.launch_persistent_context(
            user_data_dir=FIREFOX_PROFILE_PATH,
            headless=HEADLESS,
        )

        try:
            listing_page = context.pages[0] if context.pages else context.new_page()
            job_links = collect_job_links(listing_page, KEYWORDS, LOCATION)
            print(f"Collected {len(job_links)} unique job links.")

            for link in job_links:
                if state.applied >= MAX_APPLY_COUNT:
                    print("Reached MAX_APPLY_COUNT, stopping.")
                    break
                process_job_link(context, link, state)

        finally:
            context.close()

    save_results(state)
    print("Completed run. Results saved to naukriapplied.csv")


if __name__ == "__main__":
    run()
