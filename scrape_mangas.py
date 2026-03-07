"""
comix_scraper.py
────────────────
Scrapes https://comix.to/title/290n2-call-of-the-spear/<ID>-chapter-<N>
for chapters 1–233, downloads all img.fit-w images (pressing the DOWN arrow
key 150 times to trigger lazy-loading), then bundles every chapter into its
own PDF.

Requirements
------------
pip install selenium pillow requests webdriver-manager

A Chromium / Chrome browser must be installed, or ChromeDriver available.
"""

import time
import requests
from pathlib import Path
from io import BytesIO

from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# ── Configuration ──────────────────────────────────────────────────────────────

CHAPTER_ID = "8097414"   # fixed ID — same for every chapter

BASE_URL_TEMPLATE = (
    "https://comix.to/title/290n2-call-of-the-spear/"
    + CHAPTER_ID + "-chapter-{chapter_num}"
)

START_CHAPTER = 49
END_CHAPTER   = 233

OUTPUT_DIR = Path("comix_output")   # root output folder
IMAGES_DIR = OUTPUT_DIR / "images"  # per-chapter image sub-folders
PDFS_DIR   = OUTPUT_DIR / "pdfs"    # per-chapter PDFs

DOWN_KEY_PRESSES = 500   # number of DOWN arrow key presses to load the page
DOWN_KEY_PAUSE   = 0.01   # seconds to wait between each key press
IMG_LOAD_WAIT    = 3     # seconds to wait after final key press before scraping
REQUEST_TIMEOUT  = 30    # seconds for image download

# ── Helpers ────────────────────────────────────────────────────────────────────

def make_driver() -> webdriver.Chrome:
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,900")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)


def press_down_to_load(driver: webdriver.Chrome) -> None:
    """Press the DOWN arrow key DOWN_KEY_PRESSES times to trigger lazy-loading."""
    body = driver.find_element(By.TAG_NAME, "body")
    for i in range(DOWN_KEY_PRESSES):
        body.send_keys(Keys.ARROW_DOWN)
        time.sleep(DOWN_KEY_PAUSE)
    time.sleep(IMG_LOAD_WAIT)


def get_image_urls(driver: webdriver.Chrome, page_url: str) -> list[str]:
    """Navigate to page_url, press DOWN 150×, then collect img.fit-w src values."""
    driver.get(page_url)
    # Wait for at least one img.fit-w to appear
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "img.fit-w"))
        )
    except Exception:
        print(f"  ⚠  No img.fit-w found on {page_url}")
        return []

    print(f"  ↕  Pressing DOWN key {DOWN_KEY_PRESSES}× to load images …")
    press_down_to_load(driver)

    imgs = driver.find_elements(By.CSS_SELECTOR, "img.fit-w")
    urls = []
    for img in imgs:
        src = img.get_attribute("src") or img.get_attribute("data-src") or ""
        src = src.strip()
        if src and src.startswith("http"):
            urls.append(src)
    return urls



def download_image(url: str, dest: Path) -> bool:
    """Download a single image to dest. Returns True on success."""
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={
            "Referer": "https://comix.to/",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        })
        resp.raise_for_status()
        img = Image.open(BytesIO(resp.content)).convert("RGB")
        img.save(str(dest), "JPEG", quality=95)
        return True
    except Exception as exc:
        print(f"    ✗ Failed to download {url}: {exc}")
        return False


def images_to_pdf(image_paths: list[Path], pdf_path: Path) -> None:
    """Merge a list of JPEG images into a single PDF."""
    if not image_paths:
        print("  ⚠  No images to combine into PDF.")
        return
    imgs = [Image.open(str(p)).convert("RGB") for p in image_paths]
    imgs[0].save(
        str(pdf_path),
        save_all=True,
        append_images=imgs[1:],
    )
    print(f"  ✔  PDF saved → {pdf_path}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    IMAGES_DIR.mkdir(exist_ok=True)
    PDFS_DIR.mkdir(exist_ok=True)

    driver = make_driver()
    try:
        for ch_num in range(START_CHAPTER, END_CHAPTER + 1):
            url = BASE_URL_TEMPLATE.format(chapter_num=ch_num)
            ch_label = f"chapter-{ch_num:03d}"
            ch_img_dir = IMAGES_DIR / ch_label
            ch_img_dir.mkdir(exist_ok=True)
            pdf_path = PDFS_DIR / f"{ch_label}.pdf"

            if pdf_path.exists():
                print(f"Chapter {ch_num}: PDF already exists, skipping.")
                continue

            print(f"\n── Chapter {ch_num}/{END_CHAPTER} ──────────────────────────────")
            print(f"  URL: {url}")

            img_urls = get_image_urls(driver, url)
            print(f"  Found {len(img_urls)} image(s).")

            local_images: list[Path] = []
            for idx, img_url in enumerate(img_urls, start=1):
                dest = ch_img_dir / f"{idx:04d}.jpg"
                if dest.exists():
                    local_images.append(dest)
                    continue
                ok = download_image(img_url, dest)
                if ok:
                    local_images.append(dest)
                    print(f"    ↓ [{idx}/{len(img_urls)}] {dest.name}")

            images_to_pdf(local_images, pdf_path)

    finally:
        driver.quit()

    print("\n✅  All done!")


if __name__ == "__main__":
    main()