"""Playwright-based Naukri recommended-jobs auto-apply bot.

Cycles through all recommendation tabs (Applies, Profile, Top Candidate,
You might like, Preferences), selects up to 5 jobs at a time using the
bulk-select checkbox, clicks "Apply 5 Job", handles the chatbot drawer,
then returns to the recommendations page and repeats until MAX_APPLY_COUNT
is reached or all tabs are exhausted.
"""

from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, TypeVar
from playwright.sync_api import Error, TimeoutError as PlaywrightTimeoutError, sync_playwright

import argparse
import json
import time
import logging
import pandas as pd
import random

# Set up logging to a file with timestamps
logging.basicConfig(
    filename=f"Logs/naukri_recommended_apply_{time.strftime('%Y-%m-%d_%H-%M-%S')}.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)
# ---------- re-use delay helpers from the original bot ----------
# If delay_utils.py is on the path, import it; otherwise fall back to stubs.


def human_delay(min_s: float, max_s: float, label: str = "") -> None:  # type: ignore[misc]
    if max_s > 0:
        t = random.uniform(min_s, max_s)
        if label:
            logger.info(f"[delay] {label}: {t:.2f}s")
        time.sleep(t)

def maybe_cooldown(applied: int, every_n: int, min_s: float, max_s: float) -> None:  # type: ignore[misc]
    if every_n > 0 and applied % every_n == 0:
        # cooldown = random.uniform(max(min_s, 5), max(max_s, 15))
        # logger.info(f"[cooldown] After {applied} applications, sleeping {cooldown:.1f}s")
        # time.sleep(cooldown)
        time.sleep(0.2)  # fixed short cooldown for testing 

T = TypeVar("T")

# ---------- User configuration ----------
FIREFOX_PROFILE_PATH = "./firefox-profile"
MAX_APPLY_COUNT = 100
CSV_FILE = "naukri_recommended_applied.csv"
QA_MEMORY_FILE = "qa_memory.json"
HEADLESS = False
DEFAULT_MIN_DELAY_SECONDS = 0.2
DEFAULT_MAX_DELAY_SECONDS = 0.8
DEFAULT_COOLDOWN_EVERY_N_SUCCESS = 2

RECOMMENDED_JOBS_URL = "https://www.naukri.com/mnjuser/recommendedjobs"

# Tab IDs in the order we want to cycle through them.
# These match the `id` attribute on the <div class="tab-wrapper"> elements.
TAB_IDS = ["apply", "profile", "top_candidate", "similar_jobs", "preference"]
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


# ------------------------------------------------------------------ helpers --

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Naukri recommended-jobs auto-apply bot")
    parser.add_argument("--min-delay-seconds", type=float, default=DEFAULT_MIN_DELAY_SECONDS)
    parser.add_argument("--max-delay-seconds", type=float, default=DEFAULT_MAX_DELAY_SECONDS)
    parser.add_argument("--cooldown-every-n-success", type=int, default=DEFAULT_COOLDOWN_EVERY_N_SUCCESS)
    return parser.parse_args()


def make_delay_config(args: argparse.Namespace) -> DelayConfig:
    min_s = max(0.0, args.min_delay_seconds)
    max_s = max(min_s, args.max_delay_seconds)
    return DelayConfig(
        min_delay_seconds=min_s,
        max_delay_seconds=max_s,
        cooldown_every_n_success=max(0, args.cooldown_every_n_success),
    )


def with_retry(fn: Callable[[], T], attempts: int = 3, delay_seconds: float = 0.5) -> T:
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


def normalize_question(question: str) -> str:
    return " ".join(question.strip().lower().split())


def load_qa_memory(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.info(f"Could not load QA memory from {path}: {exc}")
        return {}
    if isinstance(content, dict):
        return {str(k): str(v) for k, v in content.items()}
    return {}

def load_template_qa_memory() -> dict[tuple[str, ...], str]:
    # Be very selective with what you add here since the bot will apply these answers to any question that contains the listed keywords as substrings.
    # TODO: select one of many answers
    # TODO: add support for answering radio/chip/checkbox questions based on keyword matches, not just free text ones
    # TODO: log job titles applied to with links and skip all job titles with certain keywords in them (e.g. "consultant", "manager", etc.)
    template_qa = {tuple(["how many years of", "experience"]) : "3 years,3-5 years,2-3 years,3-5,1-3 years,1-3",
                   tuple(["do you have", "experience", "in"]) : "yes",
                   tuple(["current","ctc"]) : "20 LPA",
                   tuple(["expected","ctc"]) : "30 LPA",
                   tuple(["are you currently"]) : "yes",
                   tuple(["current location"]) : "Bangalore",
                   tuple(["currently", "residing"]) : "Bengaluru,Bangalore",
                   tuple(["willing to relocate"]) : "yes",
                   tuple(["notice", "period"]) : "2 months,1 month,30 days",
                   tuple(["pan", "number"]) : "HWULX6881T",}
    return template_qa

def save_qa_memory(path: Path, qa_memory: dict[str, str]) -> None:
    path.write_text(json.dumps(qa_memory, indent=2, sort_keys=True), encoding="utf-8")


def get_or_capture_answer(question: str, qa_memory: dict[str, str], memory_path: Path) -> tuple[str, int]:
    key = normalize_question(question)
    
    template_qa = load_template_qa_memory()
    for keywords, answer in template_qa.items():
        if all(kw in key for kw in keywords):
            logger.info(f"[QA Memory] Using template answer for keywords {keywords}: {answer!r}")
            return answer, 1
    if key in qa_memory:
        answer = qa_memory[key]
        logger.info(f"[QA Memory] Using stored answer: {question!r} -> {answer!r}")
        return answer, 0
    answer = input(f"[QA Memory] Enter answer for: {question}\n> ").strip()
    qa_memory[key] = answer
    save_qa_memory(memory_path, qa_memory)
    logger.info(f"[QA Memory] Saved new answer for: {question!r}")
    return answer, 0


# --------------------------------------------------------------- chatbot ----

def handle_chatbot_flow(
    page,
    job_label: str,
    qa_memory: dict[str, str],
    memory_path: Path,
    delay_config: DelayConfig,
) -> bool:
    """Drive the Naukri chatbot drawer to completion.

    Returns True when the application is confirmed, False otherwise.
    Reused verbatim logic from the original bot; only `job_url` renamed to
    `job_label` since we may not always have a URL here.
    """
    try:
        drawer = page.locator("div.chatbot_DrawerContentWrapper")
        drawer.wait_for(state="visible", timeout=10_000)
    except PlaywrightTimeoutError:
        return False

    logger.info(f"Chatbot flow detected for: {job_label}")

    max_question_cycles = 20
    max_retries_per_question = 3
    question_wait_timeout_ms = 8_000
    applied_confirmation_timeout_ms = 4_000

    def application_confirmed(timeout_ms: int) -> bool:
        applied_text = page.locator("div.job-title-text", has_text="Applied to")
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
    
    def debug_text_input(drawer) -> None:
        """Run this to see exactly what's in the DOM before trying to fill anything."""
        
        # 1. Find ALL contenteditable elements (visible or not)
        all_editables = drawer.locator("div[contenteditable='true']").all()
        logger.info(f"Found {len(all_editables)} contenteditable divs total")
        for i, el in enumerate(all_editables):
            try:
                logger.info(f"  [{i}] visible={el.is_visible()}, id={el.get_attribute('id')}, class={el.get_attribute('class')}, box={el.bounding_box()}")
            except Exception as e:
                logger.info(f"  [{i}] error: {e}")

        # 2. Check if drawer itself is actually scoped correctly
        logger.info(f"\nDrawer element tag: {drawer.evaluate('el => el.tagName') if hasattr(drawer, 'evaluate') else 'N/A (locator)'}")
        
        # 3. Try locating directly on page instead of drawer
        page = drawer.page if hasattr(drawer, 'page') else None
        if page:
            page_editables = page.locator("div[contenteditable='true']").all()
            logger.info(f"\nFrom PAGE scope: Found {len(page_editables)} contenteditable divs")
            for i, el in enumerate(page_editables):
                try:
                    logger.info(f"  [{i}] visible={el.is_visible()}, id={el.get_attribute('id')}, class={el.get_attribute('class')}")
                except Exception as e:
                    logger.info(f"  [{i}] error: {e}")

        # 4. Check if the element is inside an iframe
        frames = drawer.page.frames if hasattr(drawer, 'page') else []
        logger.info(f"\nNumber of frames on page: {len(frames)}")
        for i, frame in enumerate(frames):
            try:
                frame_editables = frame.locator("div[contenteditable='true']").all()
                if frame_editables:
                    logger.info(f"  Frame [{i}] url={frame.url} has {len(frame_editables)} editables!")
            except Exception as e:
                logger.info(f"  Frame [{i}] error: {e}")

        # 5. Try a raw JS inject to find and fill the element from page root
        if page:
            result = page.evaluate("""
                () => {
                    const els = document.querySelectorAll("div[contenteditable='true']");
                    return Array.from(els).map(el => ({
                        id: el.id,
                        className: el.className,
                        visible: !!(el.offsetWidth || el.offsetHeight),
                        placeholder: el.dataset.placeholder,
                        rect: el.getBoundingClientRect()
                    }));
                }
            """)
            logger.info(f"\nJS querySelectorAll found: {result}")
    
    def submit_text_answer(answers: list) -> bool:
        answer = answers[0] if answers else ""
        logger.info(f"Trying to fill text answer: {answer!r}")
        editable = drawer.locator(
            "div[contenteditable='true']:visible, div.textArea[contenteditable='true']:visible"
        ).first
        try:
            editable.wait_for(state="visible", timeout=2_500)

            # Step 1: Click to focus and trigger inputContainer-focus class
            editable.click()
            # human_delay(0.1, 0.2, "post-click")

            # Step 2: Clear existing content, then type naturally so Naukri's JS picks it up
            editable.evaluate("(el) => { el.innerText = ''; el.dispatchEvent(new Event('input', {bubbles: true})); }")
            editable.type(answer, delay=40)  # delay mimics human typing, triggers input events per keystroke

            # human_delay(0.1, 0.2, "post-type")

            # Step 3: Try clicking the send/save button (it's a div, not a <button>)
            # Matches: div.sendMsg, div[class*='send']:not([class*='disabled'])
            send_clicked = False
            for send_sel in [
                "div.sendMsg:visible",
                "div[class*='send']:not([class*='disabled']):visible",
                "[id*='sendMsg']:visible",
            ]:
                try:
                    btn = drawer.locator(send_sel).first
                    btn.wait_for(state="visible", timeout=1_500)
                    btn.click(timeout=1_500)
                    send_clicked = True
                    break
                except Exception:
                    continue

            # Step 4: Fallback to Enter if no send button was clickable
            if not send_clicked:
                try:
                    editable.press("Enter")
                except Exception:
                    pass

            # human_delay(delay_config.min_delay_seconds, delay_config.max_delay_seconds, "post-text-answer")
            return True

        except (PlaywrightTimeoutError, Error):
            pass

        # Fallback: classic inputs (textarea / input)
        text_input = drawer.locator(
            "textarea:visible, input[type='text']:visible, input:not([type]):visible"
        ).first
        try:
            text_input.wait_for(state="visible", timeout=2_500)
            try:
                text_input.fill(answer)
            except Exception:
                try:
                    text_input.evaluate(
                        "(el, val) => { el.value = val; el.dispatchEvent(new Event('input', {bubbles:true})); }",
                        answer,
                    )
                except Exception:
                    pass
            try:
                text_input.press("Enter")
            except Exception:
                pass
            # human_delay(delay_config.min_delay_seconds, delay_config.max_delay_seconds, "post-text-answer")
            return True
        except (PlaywrightTimeoutError, Error):
            pass

        return False

    def submit_chip_answer(question: str, answer: list) -> bool:
        logger.info(f"Trying to submit chip answers: {answer!r} for question: {question!r}")
        chips = drawer.locator("div.chatbot_Chip.chipItem:visible")
        chip_count = chips.count()
        if chip_count == 0:
            return False

        def norm(v: str) -> str:
            return " ".join(v.strip().lower().split())
        for ans in answer:
            normalized = norm(ans)
            logger.info(f"  Normalized answer to match against chips: {norm(ans)!r}")
            for idx in range(chip_count):
                chip = chips.nth(idx)
                try:
                    chip_text = norm(chip.inner_text(timeout=1_000))
                except (PlaywrightTimeoutError, Error):
                    continue
                if chip_text == normalized:
                    try:
                        chip.click(timeout=2_000)
                        # human_delay(delay_config.min_delay_seconds, delay_config.max_delay_seconds, "post-chip")
                        return True
                    except (PlaywrightTimeoutError, Error):
                        return False

        available: list[str] = []
        for idx in range(chip_count):
            try:
                available.append(chips.nth(idx).inner_text(timeout=1_000).strip())
            except (PlaywrightTimeoutError, Error):
                pass

        corrected = input(
            f"[QA Memory] No chip match for: {question!r}\n"
            f"Available chips: {available}\n"
            "Enter chip label exactly:\n> "
        ).strip()
        if not corrected:
            return False

        qa_memory[normalize_question(question)] = corrected
        save_qa_memory(memory_path, qa_memory)

        normalized_corrected = norm(corrected)
        for idx in range(chip_count):
            chip = chips.nth(idx)
            try:
                if norm(chip.inner_text(timeout=1_000)) == normalized_corrected:
                    chip.click(timeout=2_000)
                    # human_delay(delay_config.min_delay_seconds, delay_config.max_delay_seconds, "post-corrected-chip")
                    return True
            except (PlaywrightTimeoutError, Error):
                continue
        return False

    def submit_radio_answer(question: str, answer: list) -> bool:
        logger.info(f"Trying to submit radio answers: {answer!r} for question: {question!r}")
        options = drawer.locator("div.ssrc__radio-btn-container input[type='radio']")
        option_count = options.count()
        if option_count == 0:
            return False

        def norm(v: str) -> str:
            return " ".join(v.strip().lower().split())

        def resolve(provided: str):
            n = norm(provided)
            if not n:
                return None
            for idx in range(option_count):
                opt = options.nth(idx)
                val = norm(opt.get_attribute("value") or "")
                label = ""
                oid = opt.get_attribute("id")
                if oid:
                    lbl = drawer.locator(f"label[for='{oid}']")
                    if lbl.count() > 0:
                        try:
                            label = norm(lbl.first.inner_text(timeout=1_000))
                        except (PlaywrightTimeoutError, Error):
                            pass
                logger.info(f"  [radio {idx}] value={val!r} label={label!r} -> match={n in {val, label}}")
                if n in {val, label}:
                    return opt, oid  # return oid so we can click the label instead
            return None, None
        
        selected, selected_id = None, None
        for ans in answer:
            selected, selected_id = resolve(ans) # type: ignore
            if selected is not None:
                break

        if selected is None:
            # TODO: need timeout or some way to break out if there are multiple answers
            corrected = input(
                f"[QA Memory] No radio match for: {question!r}\n"
                "Enter option label/value exactly:\n> "
            ).strip()
            if not corrected:
                return False
            qa_memory[normalize_question(question)] = corrected
            save_qa_memory(memory_path, qa_memory)
            selected, selected_id = resolve(corrected) # type: ignore
            if selected is None:
                return False

        # Click the label instead of checking the hidden input directly
        clicked = False
        if selected_id:
            try:
                label = drawer.locator(f"label[for='{selected_id}']")
                label.first.click(timeout=2_000)
                clicked = True
            except (PlaywrightTimeoutError, Error):
                pass

        if not clicked:
            # Fallback: force-check via JS in case input is hidden
            try:
                selected.evaluate("el => el.click()")
                clicked = True
            except Exception:
                pass

        if not clicked:
            try:
                selected.check(force=True, timeout=2_000)
                clicked = True
            except (PlaywrightTimeoutError, Error):
                return False

        # human_delay(0.1, 0.1, "post-radio-click")

        # Save button — try multiple selectors since nesting makes it tricky
        saved = False
        for sel in [
            "[id*='sendMsg'] div.sendMsg",      # div.sendMsg nested inside the sendMsg container
            "div.sendMsg",                       # bare class
            "[id*='sendMsg']:not([class*='disabled'])",  # the outer wrapper when not disabled
        ]:
            try:
                btn = drawer.locator(sel).first
                btn.wait_for(state="visible", timeout=1_500)
                btn.click(timeout=1_500)
                saved = True
                break
            except (PlaywrightTimeoutError, Error):
                continue

        if saved:
            # human_delay(delay_config.min_delay_seconds, delay_config.max_delay_seconds, "post-save-radio")
            return True

        return False

    def submit_checkbox_answer(question: str, answer: list) -> bool:
        logger.info(f"Trying to submit checkbox answer: {answer!r} for question: {question!r}")
        checkboxes = drawer.locator("div.multicheckboxes-container input[type='checkbox']")
        checkbox_count = checkboxes.count()
        if checkbox_count == 0:
            return False

        def norm(v: str) -> str:
            return " ".join(v.strip().lower().split())

        # Answer may be comma-separated for multi-select e.g. "Yes, No"
        desired = {norm(a) for a in answer if a.strip()}

        matched = []
        for idx in range(checkbox_count):
            cb = checkboxes.nth(idx)
            val = norm(cb.get_attribute("value") or "")
            label = ""
            cid = cb.get_attribute("id")
            if cid:
                lbl = drawer.locator(f"label[for='{cid}']")
                if lbl.count() > 0:
                    try:
                        label = norm(lbl.first.inner_text(timeout=1_000))
                    except (PlaywrightTimeoutError, Error):
                        pass
            logger.info(f"  [checkbox {idx}] value={val!r} label={label!r}")
            if desired & {val, label}:  # any overlap
                matched.append((cb, cid))

        if not matched:
            corrected = input(
                f"[QA Memory] No checkbox match for: {question!r}\n"
                f"Available options logger.infoed above. Enter comma-separated labels/values:\n> "
            ).strip()
            if not corrected:
                return False
            qa_memory[normalize_question(question)] = corrected
            save_qa_memory(memory_path, qa_memory)
            desired = {norm(a) for a in corrected.split(",") if a.strip()}
            matched = []
            for idx in range(checkbox_count):
                cb = checkboxes.nth(idx)
                val = norm(cb.get_attribute("value") or "")
                cid = cb.get_attribute("id")
                label = ""
                if cid:
                    lbl = drawer.locator(f"label[for='{cid}']")
                    if lbl.count() > 0:
                        try:
                            label = norm(lbl.first.inner_text(timeout=1_000))
                        except (PlaywrightTimeoutError, Error):
                            pass
                if desired & {val, label}:
                    matched.append((cb, cid))
            if not matched:
                return False

        # Click each matched label (same trick as radio — inputs may be visually hidden)
        for cb, cid in matched:
            clicked = False
            if cid:
                try:
                    drawer.locator(f"label[for='{cid}']").first.click(timeout=2_000)
                    clicked = True
                except (PlaywrightTimeoutError, Error):
                    pass
            if not clicked:
                try:
                    cb.evaluate("el => el.click()")
                    clicked = True
                except Exception:
                    pass
            if not clicked:
                try:
                    cb.check(force=True, timeout=2_000)
                except (PlaywrightTimeoutError, Error):
                    pass

        # human_delay(0.2, 0.4, "post-checkbox-click")

        # Save button — same selectors as radio
        for sel in [
            "[id*='sendMsg'] div.sendMsg",
            "div.sendMsg",
            "[id*='sendMsg']:not([class*='disabled'])",
        ]:
            try:
                btn = drawer.locator(sel).first
                btn.wait_for(state="visible", timeout=1_500)
                btn.click(timeout=1_500)
                # human_delay(delay_config.min_delay_seconds, delay_config.max_delay_seconds, "post-save-checkbox")
                return True
            except (PlaywrightTimeoutError, Error):
                continue

        return False

    seen_attempts: dict[str, int] = {}

    for _ in range(max_question_cycles):
        if application_confirmed(timeout_ms=1_000):
            return True

        latest_q = ""
        try:
            drawer.locator("div.botMsg.msg, div.botMsg, div.msg.botMsg").last.wait_for(
                state="visible", timeout=question_wait_timeout_ms
            )
            latest_q = extract_latest_question()
        except PlaywrightTimeoutError:
            if application_confirmed(timeout_ms=applied_confirmation_timeout_ms):
                return True
            continue

        if not latest_q:
            if application_confirmed(timeout_ms=applied_confirmation_timeout_ms):
                return True
            continue

        if "thank you for your responses" in latest_q.lower():
            return application_confirmed(timeout_ms=12_000)

        attempts = seen_attempts.get(latest_q, 0)
        if attempts >= max_retries_per_question:
            logger.info(f"Retry limit for question: {latest_q!r}")
            return False
        seen_attempts[latest_q] = attempts + 1
        # debug_text_input(drawer)  # <-- run this to debug why text input might not be working
        answer, temp_bool = get_or_capture_answer(latest_q, qa_memory, memory_path)
        
        # TODO: add ability to apply answers for substring matches in radio, chip and checkbox handlers as well
        if temp_bool == 1:
            answers = [x.strip() for x in answer.split(",")]
            logger.info(f"Using template answer split into: {answers} with question: {latest_q!r}")
        else:
            answers = [answer]
        
        # Define your submission strategies in order of priority
        submission_methods = [
            lambda ans: submit_text_answer(ans),
            lambda ans: submit_radio_answer(latest_q, ans),
            lambda ans: submit_chip_answer(latest_q, ans),
            lambda ans: submit_checkbox_answer(latest_q, ans),
        ]

        for submit in submission_methods:
            if submit(answers):
               break
        else:
            logger.info(f"No handler matched for question: {latest_q!r}")
            # TODO: IDK what should be here?
    return application_confirmed(timeout_ms=8_000)


# --------------------------------------------------------- tab navigation ---

def switch_tab(page, tab_id: str) -> bool:
    """Click the tab with the given id and wait for content to render.

    Returns True on success, False if the tab element could not be found.
    """
    tab = page.locator(f"div.tab-wrapper#{tab_id}")
    try:
        tab.wait_for(state="visible", timeout=5_000)
        tab.click()
        # human_delay(0.5, 1.0, f"after clicking tab {tab_id}")
        # Wait for at least one job tuple or a "no jobs" indicator.
        page.wait_for_load_state("networkidle", timeout=10_000)
        return True
    except (PlaywrightTimeoutError, Error) as exc:
        logger.info(f"Could not switch to tab {tab_id!r}: {exc}")
        return False


# ------------------------------------------------------- job selection ------

def select_next_batch(page, batch_size: int = 5) -> list[str]:
    """Select up to `batch_size` un-applied jobs using the checkbox icon.

    The checkbox icon class on a *deselected* job tuple is:
        <i class="dspIB naukicon naukicon-ot-checkbox">
    When selected it becomes:
        <i class="dspIB naukicon naukicon-ot-Checked">

    We click the checkbox container (<div class="saveJobContainer tuple-check-box">)
    for each job we want to include, then return the list of selected job IDs
    (taken from the parent article's data-job-id attribute) for logging.
    """
    # Locate all unselected job checkboxes currently visible on the page.
    unselected = page.locator(
        "article.jobTuple div.saveJobContainer.tuple-check-box:has(i.naukicon-ot-checkbox)"
    )

    try:
        unselected.first.wait_for(state="visible", timeout=8_000)
    except PlaywrightTimeoutError:
        logger.info("No selectable job tuples found on this tab.")
        return []

    count = unselected.count()
    if count == 0:
        logger.info("No unselected jobs available.")
        return []

    to_select = min(count, batch_size)
    selected_ids: list[str] = []

    for idx in range(to_select):
        checkbox_div = unselected.nth(idx)
        try:
            # Retrieve the parent article's job id for logging before clicking.
            article = page.locator(
                "article.jobTuple div.saveJobContainer.tuple-check-box:has(i.naukicon-ot-checkbox)"
            ).nth(idx)
            # Walk up to the article element to read data-job-id.
            job_id = checkbox_div.evaluate(
                "el => el.closest('article[data-job-id]')?.getAttribute('data-job-id') || 'unknown'"
            )
            checkbox_div.click(timeout=3_000)
            selected_ids.append(job_id)
            logger.info(f"  Selected job {job_id} ({idx + 1}/{to_select})")
            time.sleep(0.15)  # tiny gap to avoid triggering rate limits
        except (PlaywrightTimeoutError, Error) as exc:
            logger.info(f"  Could not select job at index {idx}: {exc}")

    return selected_ids


def click_bulk_apply_button(page) -> bool:
    """Click the 'Apply N Job' multi-apply button.

    The button text varies with the count ("Apply 1 Job", "Apply 5 Job", etc.)
    so we match by class rather than exact text.
    """
    btn = page.locator("button.multi-apply-button")
    try:
        btn.wait_for(state="visible", timeout=10_000)
        btn_text = btn.inner_text().strip()
        logger.info(f"Clicking bulk apply button: {btn_text!r}")
        btn.click(timeout=3_000)
        return True
    except (PlaywrightTimeoutError, Error) as exc:
        logger.info(f"Bulk apply button not found or not clickable: {exc}")
        return False


# --------------------------------------------------------- chatbot multi ----

def handle_post_bulk_apply(
    page,
    selected_ids: list[str],
    state: ApplyState,
    qa_memory: dict[str, str],
    memory_path: Path,
    delay_config: DelayConfig,
) -> None:
    """After clicking the bulk Apply button, handle whatever Naukri shows.

    Naukri may:
      1. Directly confirm all N applications (success banner).
      2. Open a single shared chatbot drawer for N jobs sequentially.
      3. Open individual chatbot drawers one after another.

    We loop until no more chatbot drawers appear or a confirmation is shown.
    """
    max_chatbot_rounds = len(selected_ids) * 2  # generous upper bound

    for round_idx in range(max_chatbot_rounds):
        # Check for a bulk-success indicator first.
        try:
            success_banner = page.locator(
                "div.job-title-text:has-text('Applied to'), "
                "div[class*='successMsg'], "
                "div[class*='apply-success']"
            )
            success_banner.first.wait_for(state="visible", timeout=2_000)
            count_applied = len(selected_ids)
            logger.info(f"Bulk apply confirmed for {count_applied} job(s).")
            state.applied += count_applied
            state.passed_links.extend([f"recommended-job-id:{jid}" for jid in selected_ids])
            return
        except PlaywrightTimeoutError:
            pass

        # Check whether a chatbot drawer is open.
        drawer = page.locator("div.chatbot_DrawerContentWrapper")
        try:
            drawer.wait_for(state="visible", timeout=3_000)
        except PlaywrightTimeoutError:
            # No drawer and no success banner — assume done.
            if round_idx == 0:
                logger.info("No confirmation and no chatbot detected after bulk apply.")
                state.failed += len(selected_ids)
                state.failed_links.extend([f"recommended-job-id:{jid}" for jid in selected_ids])
            return

        # Determine which job the chatbot is for (Naukri shows the job title in
        # the drawer header when applying from recommendations).
        try:
            drawer_title = drawer.locator("div.job-title-text, div[class*='jobTitle']").first
            job_label = drawer_title.inner_text(timeout=2_000).strip()
        except (PlaywrightTimeoutError, Error):
            job_label = f"bulk-job-{round_idx + 1}"

        logger.info(f"[Round {round_idx + 1}] Handling chatbot for: {job_label!r}")
        success = handle_chatbot_flow(page, job_label, qa_memory, memory_path, delay_config)

        if success:
            state.applied += 1
            state.passed_links.append(f"recommended:{job_label}")
            logger.info(f"Applied successfully: {job_label} | Total: {state.applied}")
        else:
            state.failed += 1
            state.failed_links.append(f"recommended:{job_label}")
            logger.info(f"Chatbot flow incomplete for: {job_label!r}")

        # human_delay(delay_config.min_delay_seconds, delay_config.max_delay_seconds, "between chatbot rounds")

        # After a chatbot completes, Naukri may close the drawer and open the
        # next one, or navigate back to the listings.  Give the page time to settle.
        try:
            drawer.wait_for(state="hidden", timeout=4_000)
        except PlaywrightTimeoutError:
            pass  # drawer might just reload for the next job

    logger.info(f"Exited chatbot loop after {max_chatbot_rounds} rounds.")


# -------------------------------------------------------------- main loop ---

def run_tab(
    page,
    tab_id: str,
    state: ApplyState,
    qa_memory: dict[str, str],
    memory_path: Path,
    delay_config: DelayConfig,
) -> None:
    """Process all batches of jobs within a single tab until exhausted or limit hit."""
    logger.info(f"\n=== Processing tab: {tab_id!r} ===")

    batch_number = 0
    while state.applied < MAX_APPLY_COUNT:
        batch_number += 1
        logger.info(f"\n--- Tab {tab_id!r}, Batch {batch_number} ---")

        selected_ids = select_next_batch(page, batch_size=5)
        if not selected_ids:
            logger.info(f"No more jobs to select in tab {tab_id!r}.")
            break

        logger.info(f"Selected {len(selected_ids)} job(s): {selected_ids}")

        if not click_bulk_apply_button(page):
            # De-select by reloading the page and try again next cycle.
            logger.info("Could not click apply button; reloading page.")
            page.reload(wait_until="domcontentloaded", timeout=20_000)
            # human_delay(1.0, 2.0, "after reload")
            if not switch_tab(page, tab_id):
                break
            continue

        handle_post_bulk_apply(page, selected_ids, state, qa_memory, memory_path, delay_config)

        maybe_cooldown(
            state.applied,
            delay_config.cooldown_every_n_success,
            delay_config.min_delay_seconds,
            delay_config.max_delay_seconds,
        )

        # Return to the recommendations page and re-activate the same tab so
        # that newly loaded / remaining jobs are visible.
        logger.info("Returning to recommendations page…")
        with_retry(lambda: page.goto(RECOMMENDED_JOBS_URL, wait_until="domcontentloaded", timeout=30_000))
        # human_delay(1.0, 2.0, "after returning to recommendations page")

        if not switch_tab(page, tab_id):
            logger.info(f"Could not re-activate tab {tab_id!r} after returning.")
            break

        # human_delay(delay_config.min_delay_seconds, delay_config.max_delay_seconds, "after tab switch")


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
        raise ValueError("Set FIREFOX_PROFILE_PATH to an existing Firefox profile directory.")

    state = ApplyState()
    memory_path = Path(QA_MEMORY_FILE)
    qa_memory = load_qa_memory(memory_path)
    logger.info(f"Loaded {len(qa_memory)} QA memory entries.")

    with sync_playwright() as playwright:
        context = playwright.firefox.launch_persistent_context(
            user_data_dir=FIREFOX_PROFILE_PATH,
            headless=HEADLESS,
            args=["--disable-gpu"],
        )

        try:
            page = context.pages[0] if context.pages else context.new_page()

            logger.info(f"Opening recommendations page: {RECOMMENDED_JOBS_URL}")
            with_retry(lambda: page.goto(RECOMMENDED_JOBS_URL, wait_until="domcontentloaded", timeout=30_000))
            human_delay(1.5, 2.5, "initial page load")

            for tab_id in TAB_IDS:
                if state.applied >= MAX_APPLY_COUNT:
                    logger.info("Reached MAX_APPLY_COUNT.")
                    break

                if not switch_tab(page, tab_id):
                    logger.info(f"Skipping tab {tab_id!r} (could not switch).")
                    continue

                run_tab(page, tab_id, state, qa_memory, memory_path, delay_config)

        finally:
            context.close()

    save_results(state)
    logger.info(
        f"\nDone. Applied: {state.applied}, Failed: {state.failed}. "
        f"Results saved to {CSV_FILE}."
    )


if __name__ == "__main__":
    run()