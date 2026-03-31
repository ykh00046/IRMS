from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = 'http://127.0.0.1:8765'
ARTIFACT_DIR = Path(__file__).resolve().parent / 'tmp_e2e_artifacts'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page_errors = []
    console_errors = []

    page.on('pageerror', lambda error: page_errors.append(str(error)))
    page.on('console', lambda msg: console_errors.append(msg.text) if msg.type == 'error' else None)

    page.goto(f'{BASE_URL}/management/login?next=%2Fmanagement', wait_until='networkidle')
    page.select_option('#manager-username', 'manager')
    page.fill('#manager-password', 'manager123')
    page.click('#management-login-submit')
    page.wait_for_url(f'{BASE_URL}/management')
    page.wait_for_load_state('networkidle')
    page.wait_for_timeout(1000)

    spreadsheet_count = page.locator('.jss_container').count()
    status = {
        'spreadsheet_count': spreadsheet_count,
        'page_errors': page_errors,
        'console_errors': console_errors,
    }
    print(status)
    page.screenshot(path=str(ARTIFACT_DIR / 'management-spreadsheet.png'), full_page=True)
    browser.close()
