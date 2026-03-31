from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

BASE_URL = 'http://127.0.0.1:8765'
ARTIFACT_DIR = Path(__file__).resolve().parent / 'tmp_e2e_artifacts'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
TOKEN = uuid4().hex[:8]
PRODUCT = f'E2E-PRODUCT-{TOKEN}'
INK = f'E2E-INK-{TOKEN}'
POSITION = 'POS-1'

DATA = [
    ['PRODUCTNAME', 'POSITION', 'INKNAME', 'BYK199', 'RED', 'BLUE'],
    [PRODUCT, POSITION, INK, '1.5', '0.2', '0.1'],
]


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


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
    page.wait_for_selector('.jss_container')

    page.evaluate(
        """
        (rows) => {
          const workbook = document.getElementById('spreadsheet').spreadsheet;
          const worksheet = workbook.worksheets[0];
          worksheet.setData(rows);
        }
        """,
        DATA,
    )

    page.click('#preview-btn')
    page.wait_for_timeout(1200)

    preview_meta = page.locator('#preview-meta').inner_text()
    preview_text = page.locator('#preview-body').inner_text()
    register_disabled = page.locator('#register-btn').is_disabled()

    assert_true('ROWS 1' in preview_meta.upper(), f'expected ROWS 1 in preview meta, got {preview_meta!r}')
    assert_true(PRODUCT in preview_text, 'preview should include imported product name')
    assert_true(INK in preview_text, 'preview should include imported ink name')
    assert_true(not register_disabled, 'register button should be enabled after successful preview')

    page.screenshot(path=str(ARTIFACT_DIR / 'management-validate.png'), full_page=True)

    page.click('#register-btn')
    page.wait_for_timeout(1200)
    page.reload(wait_until='networkidle')
    page.wait_for_selector('#history-body')
    page.wait_for_timeout(800)

    history_text = page.locator('#history-body').inner_text()
    register_disabled_after = page.locator('#register-btn').is_disabled()

    assert_true(PRODUCT in history_text, f'history should include newly registered product, got: {history_text!r}')
    assert_true(register_disabled_after, 'register button should reset to disabled after clear/reload')
    assert_true(not page_errors, f'unexpected page errors: {page_errors}')
    assert_true(not console_errors, f'unexpected console errors: {console_errors}')

    page.screenshot(path=str(ARTIFACT_DIR / 'management-register.png'), full_page=True)

    print({
        'product': PRODUCT,
        'ink': INK,
        'position': POSITION,
        'preview_meta': preview_meta,
        'page_errors': page_errors,
        'console_errors': console_errors,
        'artifacts': [
            str(ARTIFACT_DIR / 'management-validate.png'),
            str(ARTIFACT_DIR / 'management-register.png'),
        ],
    })

    browser.close()
