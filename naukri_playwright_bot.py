"""Playwright-based Naukri auto-apply bot.

This module migrates the legacy Selenium flow to Playwright sync API while preserving
profile-based login reuse and adding robust waits/retries.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

import pandas as pd
from playwright.sync_api import Error, TimeoutError as PlaywrightTimeoutError, sync_playwright

from delay_utils import human_delay, maybe_cooldown

T = TypeVar("T")

# ---------- User configuration ----------
FIREFOX_PROFILE_PATH = "C:\\Users\\Kunal Vartia\\AppData\\Roaming\\Mozilla\\Firefox\\Profiles\\ynlhysj3.NaukriBot"  # Existing Firefox profile path used for persisted login.
MAX_APPLY_COUNT = 100
CSV_FILE = "naukriapplied.csv"
QA_MEMORY_FILE = "qa_memory.json"
HEADLESS = False
DEFAULT_MIN_DELAY_SECONDS = 0.6
DEFAULT_MAX_DELAY_SECONDS = 1.6
DEFAULT_COOLDOWN_EVERY_N_SUCCESS = 0
# ---------------------------------------


@dataclass
class ApplyState:
    applied: int = 0
    failed: int = 0
    passed_links: list[str] = field(default_factory=list)
    failed_links: list[str] = field(default_factory=list)


@dataclass
class DelayConfig:
    min_delay_seconds: float = DEFAULT_MIN_DELAY_SECONDS
    max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS
    cooldown_every_n_success: int = DEFAULT_COOLDOWN_EVERY_N_SUCCESS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Playwright-based Naukri auto-apply bot")
    parser.add_argument(
        "--min-delay-seconds",
        type=float,
        default=DEFAULT_MIN_DELAY_SECONDS,
        help="Minimum randomized delay (seconds) for each human-like wait.",
    )
    parser.add_argument(
        "--max-delay-seconds",
        type=float,
        default=DEFAULT_MAX_DELAY_SECONDS,
        help="Maximum randomized delay (seconds) for each human-like wait.",
    )
    parser.add_argument(
        "--cooldown-every-n-success",
        type=int,
        default=DEFAULT_COOLDOWN_EVERY_N_SUCCESS,
        help="Apply an extra cooldown after every N successful applications (0 disables).",
    )
    return parser.parse_args()


def make_delay_config(args: argparse.Namespace) -> DelayConfig:
    min_delay_seconds = max(0.0, args.min_delay_seconds)
    max_delay_seconds = max(min_delay_seconds, args.max_delay_seconds)
    cooldown_every_n_success = max(0, args.cooldown_every_n_success)
    return DelayConfig(
        min_delay_seconds=min_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        cooldown_every_n_success=cooldown_every_n_success,
    )


def normalize_question(question: str) -> str:
    """Normalize question text for consistent memory lookups."""
    return " ".join(question.strip().lower().split())


def load_qa_memory(path: Path) -> dict[str, str]:
    """Load persisted question-answer memory from disk."""
    if not path.exists():
        return {}

    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Could not load QA memory from {path}: {exc}")
        return {}

    if isinstance(content, dict):
        return {str(k): str(v) for k, v in content.items()}
    return {}


def save_qa_memory(path: Path, qa_memory: dict[str, str]) -> None:
    """Persist question-answer memory to disk."""
    path.write_text(json.dumps(qa_memory, indent=2, sort_keys=True), encoding="utf-8")


def get_or_capture_answer(question: str, qa_memory: dict[str, str], memory_path: Path) -> str:
    """Return answer from memory or capture and persist a new answer from terminal input."""
    key = normalize_question(question)
    if key in qa_memory:
        answer = qa_memory[key]
        print(f"[QA Memory] Using stored answer for question: {question} -> {answer}")
        return answer

    answer = input(f"[QA Memory] Enter answer for question: {question}\n> ").strip()
    qa_memory[key] = answer
    save_qa_memory(memory_path, qa_memory)
    print(f"[QA Memory] Captured and saved new answer for question: {question}")
    return answer


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


FILTERED_LISTING_URL_TEMPLATE = (
    "https://www.naukri.com/software-artificial-intelligence-genai-ai-ml-jobs-{page}"
    "?k=software%20artificial%20intelligence%20genai%20ai%20ml&experience=6"
)


def build_filtered_url(page_index: int) -> str:
    """Build listing URL for saved filters with page number as `page_index + 1`."""
    return FILTERED_LISTING_URL_TEMPLATE.format(page=page_index + 1)


def collect_job_links(listing_page) -> list[str]:
    links: list[str] = []

    for page_index in range(21):  # i = 0..20
        url = build_filtered_url(page_index)
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


def handle_chatbot_flow(
    job_page,
    job_url: str,
    qa_memory: dict[str, str],
    memory_path: Path,
    delay_config: DelayConfig,
) -> bool:
    """Handle chatbot drawer flow in a dedicated function.

    Returns True if application appears to be completed, else False.
    """
    try:
        drawer = job_page.locator("div.chatbot_DrawerContentWrapper")
        drawer.wait_for(state="visible", timeout=10_000)
    except PlaywrightTimeoutError:
        return False

    print(f"Chatbot flow detected for: {job_url}")

    max_question_cycles = 20
    max_retries_per_question = 3
    question_wait_timeout_ms = 8_000
    applied_confirmation_timeout_ms = 4_000

    def application_confirmed(timeout_ms: int) -> bool:
        applied_text = job_page.locator("div.job-title-text", has_text="Applied to")
        try:
            applied_text.first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except PlaywrightTimeoutError:
            return False

    def extract_latest_question() -> str:
        candidates = drawer.locator("div.botMsg.msg, div.botMsg, div.msg.botMsg")
        count = candidates.count()
        for idx in range(count - 1, -1, -1):
            try:
                text = candidates.nth(idx).inner_text(timeout=1_500).strip()
            except (PlaywrightTimeoutError, Error):
                continue
            if text:
                return text
        return ""

    def submit_text_answer(answer: str) -> bool:
        text_input = drawer.locator(
            "textarea:visible, input[type='text']:visible, input:not([type]):visible"
        ).first
        try:
            text_input.wait_for(state="visible", timeout=2_500)
            text_input.fill(answer)
            text_input.press("Enter")
            human_delay(
                delay_config.min_delay_seconds,
                delay_config.max_delay_seconds,
                "post-send text answer delay",
            )
            return True
        except (PlaywrightTimeoutError, Error):
            pass

        send_button = drawer.locator("button:visible", has_text="Send")
        try:
            send_button.first.click(timeout=1_500)
            human_delay(
                delay_config.min_delay_seconds,
                delay_config.max_delay_seconds,
                "post-click Send button delay",
            )
            return True
        except (PlaywrightTimeoutError, Error):
            return False

    def submit_radio_answer(question: str, answer: str) -> bool:
        options = drawer.locator("div.ssrc__radio-btn-container input[type='radio']:visible")
        option_count = options.count()
        if option_count == 0:
            return False

        def normalize_option(value: str) -> str:
            return " ".join(value.strip().lower().split())

        def resolve_matching_option(provided_answer: str):
            normalized_answer = normalize_option(provided_answer)
            if not normalized_answer:
                return None

            for idx in range(option_count):
                option = options.nth(idx)
                option_value = normalize_option(option.get_attribute("value") or "")

                option_label = ""
                option_id = option.get_attribute("id")
                if option_id:
                    label_locator = drawer.locator(f"label[for='{option_id}']")
                    if label_locator.count() > 0:
                        try:
                            option_label = normalize_option(label_locator.first.inner_text(timeout=1_000))
                        except (PlaywrightTimeoutError, Error):
                            option_label = ""

                if normalized_answer in {option_value, option_label}:
                    return option
            return None

        selected_option = resolve_matching_option(answer)
        if selected_option is None:
            corrected_answer = input(
                f"[QA Memory] No exact radio option match found for question: {question}\n"
                "Enter corrected option label/value exactly as shown:\n> "
            ).strip()

            if not corrected_answer:
                return False

            key = normalize_question(question)
            qa_memory[key] = corrected_answer
            save_qa_memory(memory_path, qa_memory)
            print(f"[QA Memory] Updated stored answer for question: {question} -> {corrected_answer}")

            selected_option = resolve_matching_option(corrected_answer)
            if selected_option is None:
                return False

        try:
            selected_option.check(timeout=2_000)
        except (PlaywrightTimeoutError, Error):
            return False

        save_control = drawer.locator("div.sendMsg[tabindex='0']:visible", has_text="Save")
        try:
            save_control.first.click(timeout=2_000)
            human_delay(
                delay_config.min_delay_seconds,
                delay_config.max_delay_seconds,
                "post-click Save button delay",
            )
            return True
        except (PlaywrightTimeoutError, Error):
            return False

    seen_question_attempts: dict[str, int] = {}

    for _ in range(max_question_cycles):
        # Direct success state can appear before any question message is rendered.
        if application_confirmed(timeout_ms=1_000):
            return True

        latest_question = ""
        try:
            drawer.locator("div.botMsg.msg, div.botMsg, div.msg.botMsg").last.wait_for(
                state="visible", timeout=question_wait_timeout_ms
            )
            latest_question = extract_latest_question()
        except PlaywrightTimeoutError:
            if application_confirmed(timeout_ms=applied_confirmation_timeout_ms):
                return True
            continue

        if not latest_question:
            if application_confirmed(timeout_ms=applied_confirmation_timeout_ms):
                return True
            continue

        if "thank you for your responses" in latest_question.lower():
            return application_confirmed(timeout_ms=12_000)

        attempts = seen_question_attempts.get(latest_question, 0)
        if attempts >= max_retries_per_question:
            print(f"Reached retry limit for question: {latest_question}")
            return False
        seen_question_attempts[latest_question] = attempts + 1

        answer = get_or_capture_answer(latest_question, qa_memory, memory_path)

        handled = submit_radio_answer(latest_question, answer)
        if not handled:
            handled = submit_text_answer(answer)
        if not handled:
            print(f"No compatible answer handler found for question: {latest_question}")
            return False

        # Allow chatbot to render next state before next polling cycle.
        human_delay(
            delay_config.min_delay_seconds,
            delay_config.max_delay_seconds,
            "between chatbot question responses",
        )

    return application_confirmed(timeout_ms=8_000)


def process_job_link(
    context,
    job_url: str,
    state: ApplyState,
    qa_memory: dict[str, str],
    memory_path: Path,
    delay_config: DelayConfig,
) -> None:
    human_delay(
        delay_config.min_delay_seconds,
        delay_config.max_delay_seconds,
        "before opening each job tab",
    )
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

        human_delay(
            delay_config.min_delay_seconds,
            delay_config.max_delay_seconds,
            "before clicking Apply",
        )
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
                applied_successfully = handle_chatbot_flow(
                    job_page,
                    job_url,
                    qa_memory,
                    memory_path,
                    delay_config,
                )
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
    args = parse_args()
    delay_config = make_delay_config(args)

    if not FIREFOX_PROFILE_PATH:
        raise ValueError("Set FIREFOX_PROFILE_PATH to your existing Firefox profile path.")

    state = ApplyState()
    memory_path = Path(QA_MEMORY_FILE)
    qa_memory = load_qa_memory(memory_path)
    print(f"Loaded {len(qa_memory)} QA memory entries from {memory_path}.")

    with sync_playwright() as playwright:
        context = playwright.firefox.launch_persistent_context(
            user_data_dir=FIREFOX_PROFILE_PATH,
            headless=HEADLESS,
        )

        try:
            listing_page = context.pages[0] if context.pages else context.new_page()
            job_links = collect_job_links(listing_page)
            print(f"Collected {len(job_links)} unique job links.")

            for link in job_links:
                if state.applied >= MAX_APPLY_COUNT:
                    print("Reached MAX_APPLY_COUNT, stopping.")
                    break

                prior_applied = state.applied
                process_job_link(context, link, state, qa_memory, memory_path, delay_config)

                if state.applied > prior_applied:
                    maybe_cooldown(
                        state.applied,
                        delay_config.cooldown_every_n_success,
                        delay_config.min_delay_seconds,
                        delay_config.max_delay_seconds,
                    )

                human_delay(
                    delay_config.min_delay_seconds,
                    delay_config.max_delay_seconds,
                    "between job completions and page transitions",
                )

        finally:
            context.close()

    save_results(state)
    print("Completed run. Results saved to naukriapplied.csv")


if __name__ == "__main__":
    run()
