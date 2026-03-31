from pathlib import Path
from uuid import uuid4

from playwright.sync_api import sync_playwright

BASE_URL = 'http://127.0.0.1:8765'
ARTIFACT_DIR = Path(__file__).resolve().parent / 'tmp_e2e_artifacts'
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
TOKEN = uuid4().hex[:8]
NOTICE_MESSAGE = f'E2E management notice {TOKEN}'
WORKFLOW_MESSAGE = f'E2E management workflow {TOKEN}'


def assert_true(condition, message):
    if not condition:
        raise AssertionError(message)


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.goto(f'{BASE_URL}/management/login?next=%2Fmanagement', wait_until='networkidle')
    page.select_option('#manager-username', 'manager')
    page.fill('#manager-password', 'manager123')
    page.click('#management-login-submit')
    page.wait_for_url(f'{BASE_URL}/management')
    page.wait_for_load_state('networkidle')
    page.wait_for_selector('#management-chat-room-tabs [data-room-key="notice"]')

    page.locator('#management-chat-room-tabs [data-room-key="notice"]').click()
    page.wait_for_timeout(300)
    notice_hidden = page.locator('#management-chat-stage-group').evaluate("el => el.classList.contains('hidden')")
    assert_true(notice_hidden, 'notice room should hide stage selector on management page')
    page.fill('#management-chat-input', NOTICE_MESSAGE)
    page.click('#management-chat-send')
    page.wait_for_selector(f'text={NOTICE_MESSAGE}')

    page.locator('#management-chat-room-tabs [data-room-key="sample_mass_production"]').click()
    page.wait_for_timeout(300)
    workflow_hidden = page.locator('#management-chat-stage-group').evaluate("el => el.classList.contains('hidden')")
    assert_true(not workflow_hidden, 'workflow room should show stage selector on management page')
    page.select_option('#management-chat-stage', 'completed')
    page.fill('#management-chat-input', WORKFLOW_MESSAGE)
    page.click('#management-chat-send')
    page.wait_for_selector(f'text={WORKFLOW_MESSAGE}')

    persisted_room = page.evaluate("window.localStorage.getItem('irms_chat_room')")
    assert_true(persisted_room == 'sample_mass_production', 'management page should persist selected room')

    page.screenshot(path=str(ARTIFACT_DIR / 'management-chat.png'), full_page=True)

    page.goto(f'{BASE_URL}/status', wait_until='networkidle')
    page.wait_for_selector('#status-chat-room-tabs [data-room-key="sample_mass_production"].active')
    page.wait_for_selector(f'text={WORKFLOW_MESSAGE}')

    stage_badges = page.locator('#status-chat-messages .status-stage-badge.stage-completed').count()
    assert_true(stage_badges >= 1, 'status page should show completed badge for management workflow message')

    print({
        'notice_message': NOTICE_MESSAGE,
        'workflow_message': WORKFLOW_MESSAGE,
        'persisted_room': persisted_room,
        'artifacts': [str(ARTIFACT_DIR / 'management-chat.png')],
    })

    browser.close()
