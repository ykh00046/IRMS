from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

BASE_URL = 'http://127.0.0.1:8765'
ARTIFACT_DIR = Path(__file__).resolve().parent / 'tmp_e2e_artifacts'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
TOKEN = uuid4().hex[:8]
WORKFLOW_MESSAGE = f'E2E operator workflow {TOKEN}'
NOTICE_MESSAGE = f'E2E operator notice {TOKEN}'


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.goto(f'{BASE_URL}/weighing/select', wait_until='networkidle')
    page.locator('[data-operator-select]').first.click()
    page.wait_for_url(f'{BASE_URL}/weighing')
    page.wait_for_load_state('networkidle')
    page.wait_for_selector('#work-chat-room-tabs [data-room-key="notice"]')

    notice_button = page.locator('#work-chat-room-tabs [data-room-key="notice"]')
    notice_button.click()
    page.wait_for_timeout(300)
    notice_hidden = page.locator('#work-chat-stage-group').evaluate("el => el.classList.contains('hidden')")
    assert_true(notice_hidden, 'notice room should hide stage selector')

    page.fill('#work-chat-input', NOTICE_MESSAGE)
    page.click('#work-chat-send')
    page.wait_for_selector(f'text={NOTICE_MESSAGE}')

    workflow_button = page.locator('#work-chat-room-tabs [data-room-key="mass_response"]')
    workflow_button.click()
    page.wait_for_timeout(300)
    workflow_hidden = page.locator('#work-chat-stage-group').evaluate("el => el.classList.contains('hidden')")
    assert_true(not workflow_hidden, 'workflow room should show stage selector')

    page.select_option('#work-chat-stage', 'in_progress')
    page.fill('#work-chat-input', WORKFLOW_MESSAGE)
    page.click('#work-chat-send')
    page.wait_for_selector(f'text={WORKFLOW_MESSAGE}')

    room_key = page.evaluate("window.localStorage.getItem('irms_chat_room')")
    assert_true(room_key == 'mass_response', f'expected localStorage room to be mass_response, got {room_key!r}')

    page.screenshot(path=str(ARTIFACT_DIR / 'work-chat.png'), full_page=True)

    page.click('#logout-btn')
    page.wait_for_url(f'{BASE_URL}/')
    page.goto(f'{BASE_URL}/management/login?next=%2Fstatus', wait_until='networkidle')

    persisted_room = page.evaluate("window.localStorage.getItem('irms_chat_room')")
    assert_true(persisted_room == 'mass_response', 'room selection should persist across logout/login within same browser context')

    page.select_option('#manager-username', 'manager')
    page.fill('#manager-password', 'manager123')
    page.click('#management-login-submit')
    page.wait_for_url(f'{BASE_URL}/status')
    page.wait_for_load_state('networkidle')
    page.wait_for_selector('#status-chat-room-tabs [data-room-key="mass_response"].active')
    page.wait_for_selector(f'text={WORKFLOW_MESSAGE}')

    status_stage_count = page.locator('#status-chat-messages .status-stage-badge.stage-in_progress').count()
    assert_true(status_stage_count >= 1, 'status page should show in_progress stage badge for workflow message')

    page.screenshot(path=str(ARTIFACT_DIR / 'status-chat.png'), full_page=True)

    print({
        'workflow_message': WORKFLOW_MESSAGE,
        'notice_message': NOTICE_MESSAGE,
        'persisted_room': persisted_room,
        'artifacts': [
            str(ARTIFACT_DIR / 'work-chat.png'),
            str(ARTIFACT_DIR / 'status-chat.png'),
        ],
    })

    browser.close()
