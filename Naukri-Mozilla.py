import csv
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


@dataclass
class RunConfig:
    profile_path: str = ""  # Add your Firefox profile root path.
    page_start: int = 1
    page_end: int = 2
    max_applications: int = 50
    page_load_delay_s: float = 3.0
    post_apply_delay_s: float = 2.0
    answer_store_path: str = "answers.json"
    summary_dir: str = "run_summaries"
    keywords: List[str] = field(default_factory=lambda: [""])
    location: str = ""
    firstname: str = ""
    lastname: str = ""


def load_answers(path: str) -> Dict[str, str]:
    answer_file = Path(path)
    if not answer_file.exists():
        logging.info("Answer store not found, continuing with empty answers: %s", answer_file)
        return {}
    try:
        return json.loads(answer_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logging.warning("Could not parse answer store %s: %s", answer_file, exc)
        return {}


def init_driver(config: RunConfig) -> webdriver.Firefox:
    try:
        profile = webdriver.FirefoxProfile(config.profile_path)
        return webdriver.Firefox(profile)
    except WebDriverException as exc:
        logging.error("Failed to initialize Firefox driver with profile '%s': %s", config.profile_path, exc)
        raise


def build_search_url(keyword: str, location: str, page: int) -> str:
    key_slug = keyword.lower().replace(" ", "-")
    if location:
        loc_slug = location.lower().replace(" ", "-")
        return f"https://www.naukri.com/{key_slug}-jobs-in-{loc_slug}-{page}"
    return f"https://www.naukri.com/{key_slug}-{page}"


def collect_job_links(driver: webdriver.Firefox, config: RunConfig) -> List[str]:
    job_links: List[str] = []
    for keyword in config.keywords:
        for page in range(config.page_start, config.page_end + 1):
            url = build_search_url(keyword, config.location, page)
            driver.get(url)
            logging.info("Scanning search page: %s", url)
            time.sleep(config.page_load_delay_s)

            soup = BeautifulSoup(driver.page_source, "html5lib")
            results = soup.find(class_="list")
            if not results:
                logging.warning("No job list container found on search page: %s", url)
                continue

            job_elements = results.find_all("article", class_="jobTuple bgWhite br4 mb-8")
            for job_elem in job_elements:
                anchor = job_elem.find("a", class_="title fw500 ellipsis")
                if anchor and anchor.get("href"):
                    job_links.append(anchor.get("href"))
                else:
                    logging.debug("Skipped malformed job card on page: %s", url)
    return job_links


def _is_present(driver: webdriver.Firefox, xpath: str) -> bool:
    try:
        driver.find_element(By.XPATH, xpath)
        return True
    except NoSuchElementException:
        return False


def _save_job_result(job_results: List[Dict[str, str]], url: str, status: str, detail: str = "") -> None:
    job_results.append({"job_url": url, "status": status, "detail": detail})


def apply_to_job(
    driver: webdriver.Firefox,
    url: str,
    config: RunConfig,
    outcomes: Dict[str, int],
    answers: Dict[str, str],
    job_results: List[Dict[str, str]],
) -> None:
    driver.get(url)
    time.sleep(config.page_load_delay_s)

    if not _is_present(driver, "//*[text()='Apply']"):
        outcomes["skipped_non_apply"] += 1
        _save_job_result(job_results, url, "skipped_non_apply", "Apply button not found")
        logging.info("Skipped (non-apply): %s", url)
        return

    try:
        WebDriverWait(driver, 8).until(EC.element_to_be_clickable((By.XPATH, "//*[text()='Apply']"))).click()
        time.sleep(config.post_apply_delay_s)
    except TimeoutException as exc:
        outcomes["failed_timeout"] += 1
        _save_job_result(job_results, url, "failed_timeout", f"Apply button timeout: {exc}")
        logging.warning("Apply timeout for %s: %s", url, exc)
        return
    except WebDriverException as exc:
        outcomes["failed_unknown"] += 1
        _save_job_result(job_results, url, "failed_unknown", f"Apply click failed: {exc}")
        logging.error("Apply click failed for %s: %s", url, exc)
        return

    if _is_present(driver, "//*[contains(text(),'Chat')]") or _is_present(driver, "//*[contains(text(),'bot')]"):
        outcomes["applied_chatbot"] += 1
        _save_job_result(job_results, url, "applied_chatbot", "Chatbot flow triggered")
    else:
        outcomes["applied_direct"] += 1
        _save_job_result(job_results, url, "applied_direct", "Direct apply click successful")

    try:
        if _is_present(driver, "//*[text()=' 1. First Name']"):
            first_name = driver.find_element(By.XPATH, "//input[@id='CUSTOM-FIRSTNAME']")
            first_name.clear()
            first_name.send_keys(config.firstname or answers.get("firstname", ""))

        if _is_present(driver, "//*[text()=' 2. Last Name']"):
            last_name = driver.find_element(By.XPATH, "//input[@id='CUSTOM-LASTNAME']")
            last_name.clear()
            last_name.send_keys(config.lastname or answers.get("lastname", ""))

        if _is_present(driver, "//*[text()='Submit and Apply']"):
            driver.find_element(By.XPATH, "//*[text()='Submit and Apply']").click()
    except NoSuchElementException as exc:
        outcomes["failed_unknown"] += 1
        _save_job_result(job_results, url, "failed_unknown", f"Application form element missing: {exc}")
        logging.warning("Application form issue for %s: %s", url, exc)
    except WebDriverException as exc:
        outcomes["failed_unknown"] += 1
        _save_job_result(job_results, url, "failed_unknown", f"Application form interaction failed: {exc}")
        logging.error("Application form interaction failed for %s: %s", url, exc)


def write_run_summary(
    config: RunConfig,
    outcomes: Dict[str, int],
    job_results: List[Dict[str, str]],
) -> None:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    summary_root = Path(config.summary_dir)
    summary_root.mkdir(parents=True, exist_ok=True)

    payload = {
        "timestamp": timestamp,
        "config": asdict(config),
        "outcomes": outcomes,
        "total_jobs": len(job_results),
        "job_results": job_results,
    }

    json_path = summary_root / f"naukri_run_summary_{timestamp}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    csv_path = summary_root / f"naukri_run_summary_{timestamp}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "job_url", "status", "detail"])
        writer.writeheader()
        for row in job_results:
            writer.writerow({"timestamp": timestamp, **row})

    logging.info("Run summary written to %s and %s", json_path, csv_path)


def run(config: Optional[RunConfig] = None) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    cfg = config or RunConfig()
    answers = load_answers(cfg.answer_store_path)

    outcomes = {
        "applied_direct": 0,
        "applied_chatbot": 0,
        "skipped_non_apply": 0,
        "failed_timeout": 0,
        "failed_unknown": 0,
    }
    job_results: List[Dict[str, str]] = []

    driver: Optional[webdriver.Firefox] = None
    try:
        driver = init_driver(cfg)
        time.sleep(cfg.page_load_delay_s)
        job_links = collect_job_links(driver, cfg)

        for url in job_links:
            successful_count = outcomes["applied_direct"] + outcomes["applied_chatbot"]
            if successful_count >= cfg.max_applications:
                logging.info("Reached max applications (%s), stopping.", cfg.max_applications)
                break

            if _is_present(driver, "//*[text()='Your daily quota has been expired.']"):
                logging.info("Naukri daily quota reached, stopping early.")
                break

            apply_to_job(driver, url, cfg, outcomes, answers, job_results)

    except WebDriverException as exc:
        logging.exception("WebDriver failure ended run: %s", exc)
    finally:
        if driver:
            driver.quit()
        write_run_summary(cfg, outcomes, job_results)


if __name__ == "__main__":
    run()
