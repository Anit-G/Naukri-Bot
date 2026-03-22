from playwright.sync_api import sync_playwright
from naukri_playwright_bot import FIREFOX_PROFILE_PATH, HEADLESS
from time import sleep

with sync_playwright() as playwright:
    context = playwright.firefox.launch_persistent_context(
        user_data_dir=FIREFOX_PROFILE_PATH,
        headless=HEADLESS,
        args=["--disable-gpu"],)
    
    page = context.new_page()
    # page.goto("https://www.naukri.com/nlogin/login")
    page.goto("https://www.naukri.com/job-listings-machine-learning-engineer-wowtownai-technologies-gurugram-2-to-7-years-190326030687?src=drecomm_dashboard_apply")
    sleep(10)
    apply_button = page.locator("#apply-button").first
    print(apply_button.count())
    apply_button.wait_for(state="visible",timeout=1_000)
    print(apply_button.inner_text().strip())
    print("Login manually, then press Enter here to close...")
    input()  # keeps script alive until you're done, press enter in the vscode shell after login is done
    
    context.close()
