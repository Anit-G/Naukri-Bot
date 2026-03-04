"""Playwright helpers for handling Naukri Easy Apply chatbot flows."""

from __future__ import annotations

import time
from typing import Any

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError


def _normalize_text(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def _resolve_answer(question: str, options: list[str], answer_store: dict[str, Any]) -> str:
    """Resolve a chatbot answer from configured mappings."""
    normalized_question = _normalize_text(question)

    for key in (question, normalized_question):
        if key in answer_store:
            answer = answer_store[key]
            return str(answer(options, question) if callable(answer) else answer).strip()

    default_answer = answer_store.get("default")
    if callable(default_answer):
        return str(default_answer(options, question)).strip()
    if isinstance(default_answer, str):
        return default_answer.strip()

    if options:
        return options[0]

    return ""


def handle_chatbot(
    page: Page,
    answer_store: dict[str, Any],
    *,
    per_job_timeout_s: int = 120,
    poll_interval_s: float = 0.75,
) -> bool:
    """
    Handle Naukri chatbot questions until application is complete or timeout.

    Returns True when completion text/state is detected, otherwise False.
    """
    deadline = time.monotonic() + per_job_timeout_s
    answered_questions: set[str] = set()

    try:
        page.wait_for_selector("div.chatbot_DrawerContentWrapper", timeout=15_000)
    except PlaywrightTimeoutError:
        return False

    while time.monotonic() < deadline:
        if page.locator("text=Thank you for your responses").count() > 0:
            return True
        if (
            page.locator("div.job-title-text", has_text="Applied to").count() > 0
            or page.locator("text=Applied to").count() > 0
        ):
            return True

        bot_messages = page.locator("div.botMsg.msg")
        if bot_messages.count() == 0:
            time.sleep(poll_interval_s)
            continue

        latest_question = bot_messages.last.inner_text().strip()
        normalized_question = _normalize_text(latest_question)
        if not normalized_question or normalized_question in answered_questions:
            time.sleep(poll_interval_s)
            continue

        radio_selector = "div.ssrc__radio-btn-container input[type='radio']"
        text_selector = "div.textArea[contenteditable='true']"
        save_selector = "[id^='sendMsg__'] div.sendMsg, div.sendMsg"

        if page.locator(radio_selector).count() > 0:
            radios = page.locator(radio_selector)
            options: list[str] = []
            chosen_radio = None
            for idx in range(radios.count()):
                radio = radios.nth(idx)
                option_text = (
                    radio.evaluate(
                        """
                        (el) => {
                            const label = el.closest('label') || document.querySelector(`label[for='${el.id}']`);
                            return (label ? label.innerText : el.value || '').trim();
                        }
                        """
                    )
                    or ""
                ).strip()
                options.append(option_text)

            answer = _resolve_answer(latest_question, options, answer_store)
            for idx in range(radios.count()):
                if _normalize_text(options[idx]) == _normalize_text(answer):
                    chosen_radio = radios.nth(idx)
                    break

            if chosen_radio is None:
                chosen_radio = radios.first

            chosen_radio.check(force=True)
            save_btn = page.locator(save_selector).last
            if save_btn.count() > 0:
                save_btn.click()

            answered_questions.add(normalized_question)
            page.wait_for_timeout(900)
            continue

        if page.locator(text_selector).count() > 0:
            answer = _resolve_answer(latest_question, [], answer_store)
            if answer:
                text_box = page.locator(text_selector).last
                text_box.click()
                page.keyboard.press("Control+A")
                page.keyboard.type(answer)
                page.keyboard.press("Enter")
                page.wait_for_timeout(300)

                # Fallback save click if Enter didn't submit.
                if page.locator("div.botMsg.msg").last.inner_text().strip() == latest_question:
                    save_btn = page.locator(save_selector).last
                    if save_btn.count() > 0:
                        save_btn.click()

                answered_questions.add(normalized_question)
                page.wait_for_timeout(900)
                continue

        time.sleep(poll_interval_s)

    return False
