from pathlib import Path
from uuid import uuid4
from playwright.sync_api import sync_playwright

BASE_URL = 'http://127.0.0.1:8765'
TOKEN = uuid4().hex[:8]
PRODUCT = f'E2E-PRODUCT-{TOKEN}'
INK = f'E2E-INK-{TOKEN}'
POSITION = 'POS-1'
DATA = [
    ['PRODUCTNAME', 'POSITION', 'INKNAME', 'BYK199', 'RED', 'BLUE'],
    [PRODUCT, POSITION, INK, '1.5', '0.2', '0.1'],
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(f'{BASE_URL}/management/login?next=%2Fmanagement', wait_until='networkidle')
    page.select_option('#manager-username', 'manager')
    page.fill('#manager-password', 'manager123')
    page.click('#management-login-submit')
    page.wait_for_url(f'{BASE_URL}/management')
    page.wait_for_load_state('networkidle')
    page.wait_for_selector('.jss_container')
    page.evaluate("""
        (rows) => {
          const workbook = document.getElementById('spreadsheet').spreadsheet;
          const worksheet = workbook.worksheets[0];
          worksheet.setData(rows);
        }
    """, DATA)
    page.click('#preview-btn')
    page.wait_for_timeout(1200)
    print('PREVIEW_META', page.locator('#preview-meta').inner_text())
    print('REGISTER_DISABLED_BEFORE', page.locator('#register-btn').is_disabled())
    page.click('#register-btn')
    page.wait_for_timeout(2500)
    print('TOASTS', page.locator('#toast-root').inner_text())
    print('REGISTER_DISABLED_AFTER', page.locator('#register-btn').is_disabled())
    print('HISTORY_TEXT', page.locator('#history-body').inner_text())
    print('BODY_TEXT_HAS_PRODUCT', PRODUCT in page.locator('body').inner_text())
    browser.close()
