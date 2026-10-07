"""Check real role-specific pages and a cross-region browser workflow.

Run after ``python scripts/local.py start``. Credentials are read from the
generated local seed file and are never logged. The default run is headless.
The workflow creates an identifiable project, invitation and review; it does
not delete or reset existing data. Use ``--read-only`` to skip those writes.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.path.insert(0, str(ROOT / '.work' / 'dev-packages'))
    from playwright.sync_api import sync_playwright

OUT = ROOT / '.local' / 'test-results' / 'ui'
ROUTES = {
    'manager.r1': ['/', '/employees', '/projects', '/search', '/assignments', '/reviews', '/events'],
    'editor.r1': ['/', '/employees', '/events'],
    'employee.r2': ['/', '/employees', '/invitations', '/assignments', '/reviews'],
    'admin.c': ['/', '/catalog'],
    'analyst.c': ['/', '/analytics', '/employees', '/projects', '/events'],
}


def login_context(browser, accounts: list[dict], login: str, report: dict):
    """Sign in through the real form and return an isolated browser context."""
    account = next(item for item in accounts if item['login'] == login)
    base = account['url'].rstrip('/')
    context = browser.new_context(viewport={'width': 1440, 'height': 1040}, device_scale_factor=1, locale='ru-RU')
    page = context.new_page()
    page.on('pageerror', lambda error: report['page_errors'].append(str(error)))
    page.on('console', lambda message: report['console_errors'].append(message.text) if message.type == 'error' else None)
    page.goto(base + '/login', wait_until='networkidle')
    if login == 'manager.r1':
        page.screenshot(path=str(OUT / 'login-desktop.png'), full_page=True)
    page.locator('#login').fill(account['login'])
    page.locator('#password').fill(account['password'])
    page.locator('.login-submit').click()
    page.wait_for_url(base + '/', timeout=20000)
    page.wait_for_selector('#view h1', timeout=20000)
    return context, page, base


def visit(page, base: str, route: str):
    """Wait for the application renderer and reject hidden load failures."""
    response = page.goto(base + route, wait_until='networkidle')
    page.wait_for_selector('#view h1', timeout=20000)
    heading = page.locator('#view h1').inner_text()
    assert response.status == 200, (route, response.status)
    assert 'Не удалось' not in heading, (route, heading)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (route, 'horizontal overflow')
    return heading


def screenshot(page, filename: str, full: bool = True):
    page.screenshot(path=str(OUT / filename), full_page=full)


def stale_banner_check(page, base: str, report: dict):
    """Check rendering of a stale-but-enabled source using an explicit API mock."""
    def stale_response(route):
        response = route.fetch()
        data = response.json()
        data['freshness'] = {'is_stale': True, 'sources': [{'source_region': 2, 'enabled': True, 'ready': True, 'is_stale': True, 'last_received_at': None}]}
        route.fulfill(response=response, json=data)
    page.route('**/api/dashboard', stale_response)
    try:
        visit(page, base, '/')
        assert 'Свежесть данных не подтверждена' in page.locator('#view .notice').inner_text()
        report['checks'].append({'check': 'stale banner', 'evidence': 'simulated API response: enabled=true, ready=true, is_stale=true'})
    finally:
        page.unroute('**/api/dashboard', stale_response)


def read_checks(browser, accounts: list[dict], report: dict):
    """Check all application roles, dialogs, real search and mobile navigation."""
    for login, routes in ROUTES.items():
        context, page, base = login_context(browser, accounts, login, report)
        for route in routes:
            heading = visit(page, base, route)
            report['checks'].append({'login': login, 'route': route, 'heading': heading})
            if login == 'manager.r1':
                screenshot(page, 'manager-' + (route.strip('/') or 'overview') + '-desktop.png', route != '/employees')
            elif route in ('/invitations', '/catalog', '/analytics'):
                screenshot(page, login.replace('.', '-') + '-' + route[1:] + '-desktop.png', route != '/catalog')
        if login == 'manager.r1':
            projects = page.request.get(base + '/api/projects').json()['items']
            if projects:
                project = next((project for project in projects if project['name'].startswith('Atlas')), projects[0])
                visit(page, base, f"/projects/{project['owner_region']}/{project['project_id']}")
                screenshot(page, 'manager-project-detail-desktop.png')
                add = page.locator('[data-action="position.create"]')
                if add.count():
                    add.click()
                    page.wait_for_selector('dialog[open] #field-title')
                    assert page.locator('dialog[open] #field-hours_per_week').get_attribute('max') == '40'
                    screenshot(page, 'position-form-desktop.png')
                    page.locator('[data-close]').first.click()
            visit(page, base, '/search')
            page.locator('#search-form button[type=submit]').click()
            page.wait_for_selector('#search-results .split-heading', timeout=20000)
            screenshot(page, 'manager-search-results-desktop.png', False)
            page.set_viewport_size({'width': 390, 'height': 844})
            visit(page, base, '/')
            screenshot(page, 'manager-overview-mobile.png')
            page.locator('#menu-toggle').click()
            assert page.locator('#sidebar').evaluate("element => element.classList.contains('open')")
            screenshot(page, 'navigation-mobile.png')
            stale_banner_check(page, base, report)
        if login == 'editor.r1':
            people = page.request.get(base + '/api/employees').json()['items']
            if people:
                employee = people[0]
                visit(page, base, f"/employees/{employee['owner_region']}/{employee['employee_id']}")
                screenshot(page, 'employee-profile-desktop.png')
                page.locator('[data-action="employee.update"]').click()
                page.wait_for_selector('dialog[open] #field-nickname')
                assert page.locator('#field-nickname').input_value() == employee['nickname']
                page.locator('[data-close]').first.click()
        context.close()


def submit_dialog(page):
    """Submit the visible form and surface the actual business error on failure."""
    page.locator('dialog[open] button[type=submit]').click()
    try:
        page.wait_for_selector('dialog[open]', state='hidden', timeout=15000)
    except Exception:
        message = page.locator('#form-error').inner_text() if page.locator('#form-error').count() else 'dialog did not close'
        raise AssertionError(message) from None
    page.wait_for_load_state('networkidle')


def poll_json(page, url: str, predicate, description: str, timeout: float = 40):
    """Observe asynchronous delivery without inventing a replication guarantee."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = page.request.get(url)
        assert response.ok, (description, response.status)
        data = response.json()
        if predicate(data):
            return data
        page.wait_for_timeout(400)
    raise AssertionError('Timed out waiting for ' + description)


def workflow_check(browser, accounts: list[dict], report: dict):
    """Create a project in R1, invite R2, accept, complete and review in the UI."""
    manager_context, manager, manager_base = login_context(browser, accounts, 'manager.r1', report)
    employee_context, employee, employee_base = login_context(browser, accounts, 'employee.r2', report)
    employee_id = employee.request.get(employee_base + '/api/me').json()['user']['employee_id']
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    project_name = 'Маяк · проверка интерфейса ' + stamp
    position_name = 'Инженер межрегиональной команды'
    visit(manager, manager_base, '/projects')
    manager.locator('[data-action="project.create"]').first.click()
    manager.locator('#field-name').fill(project_name)
    manager.locator('#field-description').fill('Воспроизводимая браузерная проверка полного межрегионального цикла.')
    manager.locator('#field-date_from').fill('2027-04-05')
    manager.locator('#field-date_to').fill('2027-04-09')
    submit_dialog(manager)
    project = next(p for p in manager.request.get(manager_base + '/api/projects').json()['items'] if p['name'] == project_name)
    project_route = f"/projects/1/{project['project_id']}"
    project_api = manager_base + '/api' + project_route
    visit(manager, manager_base, project_route)
    manager.locator('[data-action="position.create"]').click()
    manager.locator('#field-title').fill(position_name)
    manager.locator('#field-hours_per_week').fill('0.5')
    manager.locator('#field-min_experience').fill('0')
    manager.locator('#field-search_mode').select_option('ALL')
    submit_dialog(manager)
    manager.locator('a[href^="/search?position_owner="]').first.click()
    manager.wait_for_selector('#search-results .split-heading')
    assert manager.locator('#search-hours').input_value() == '0.5', 'position requirements are not shown'
    assert manager.locator('[data-mode="ALL"]').get_attribute('aria-pressed') == 'true', 'saved search mode was not used'
    manager.locator('[data-mode="LOCAL_FIRST"]').click()
    manager.locator('#search-form button[type=submit]').click()
    manager.wait_for_selector('#search-results .split-heading')
    position = manager.request.get(project_api).json()['positions'][0]
    assert position['search_mode'] == 'OWN_FIRST', 'search mode change was not persisted'
    visit(manager, manager_base, project_route)
    manager.locator('a[href^="/search?position_owner="]').first.click()
    manager.wait_for_selector('#search-results .split-heading')
    assert manager.locator('[data-mode="LOCAL_FIRST"]').get_attribute('aria-pressed') == 'true', 'saved search mode was not restored'
    screenshot(manager, 'workflow-position-search.png', False)
    visit(manager, manager_base, f'/employees/2/{employee_id}')
    manager.locator('[data-action="invite"]').click()
    manager.wait_for_selector('dialog[open] #field-position_id')
    manager.locator('#field-position_id').select_option(label=project_name + ' · ' + position_name)
    submit_dialog(manager)
    inbox = poll_json(employee, employee_base + '/api/invitations', lambda data: any(i['project_id'] == project['project_id'] and i['status'] == 'PENDING' for i in data['items']), 'invitation delivery')
    invitation = next(i for i in inbox['items'] if i['project_id'] == project['project_id'])
    visit(employee, employee_base, '/invitations')
    card = employee.locator('.invitation-card').filter(has_text=project_name)
    card.get_by_role('button', name='Принять', exact=True).click()
    screenshot(employee, 'workflow-accept-dialog.png')
    submit_dialog(employee)
    poll_json(manager, project_api, lambda data: any(i['invitation_id'] == invitation['invitation_id'] and i['status'] == 'ACCEPTED' for i in data['invitations']), 'acceptance receipt')
    visit(manager, manager_base, project_route)
    manager.get_by_role('button', name='Запустить', exact=True).click()
    submit_dialog(manager)
    poll_json(manager, manager_base + '/api/assignments', lambda data: any(a['invitation_id'] == invitation['invitation_id'] and a['status'] == 'ACTIVE' for a in data['items']), 'assignment replication')
    visit(manager, manager_base, '/assignments')
    row = manager.locator('.data-table tbody tr').filter(has_text=project_name)
    row.get_by_role('button', name='Завершить', exact=True).click()
    submit_dialog(manager)
    poll_json(manager, project_api, lambda data: any(i['invitation_id'] == invitation['invitation_id'] and i['status'] == 'COMPLETED' for i in data['invitations']), 'completion receipt')
    poll_json(manager, manager_base + '/api/assignments', lambda data: any(a['invitation_id'] == invitation['invitation_id'] and a['status'] == 'COMPLETED' for a in data['items']), 'completed assignment replication')
    visit(manager, manager_base, '/assignments')
    row = manager.locator('.data-table tbody tr').filter(has_text=project_name)
    row.get_by_role('button', name='Оставить отзыв', exact=True).click()
    manager.locator('#field-rating').select_option('5')
    manager.locator('#field-comment').fill('Отличная совместная работа: задача завершена, договорённости соблюдены. Проверено полным браузерным сценарием.')
    submit_dialog(manager)
    detail = manager.request.get(project_api).json()
    assert any(r['assignment_id'] == invitation['assignment_id'] for r in detail['reviews']) if invitation.get('assignment_id') else bool(detail['reviews'])
    visit(manager, manager_base, project_route)
    manager.get_by_role('button', name='Завершить', exact=True).first.click()
    submit_dialog(manager)
    final = manager.request.get(project_api).json()
    assert final['project']['status'] == 'COMPLETED'
    screenshot(manager, 'workflow-completed-project.png')
    report['workflow'] = {'status': 'passed', 'project_id': project['project_id'], 'invitation_id': invitation['invitation_id'], 'project_status': final['project']['status'], 'review_count': len(final['reviews']), 'search_mode_persisted': True, 'steps': ['project.create', 'position.create', 'search', 'position.search_mode.persist', 'invitation.create', 'invitation.respond', 'project.transition.ACTIVE', 'assignment.complete', 'review.create', 'project.transition.COMPLETED']}
    manager_context.close()
    employee_context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headed', action='store_true', help='Show the browser window for interactive inspection.')
    parser.add_argument('--browser-executable', help='Use an already installed Chromium executable instead of the Playwright-managed build.')
    parser.add_argument('--read-only', action='store_true', help='Skip the workflow that creates demonstration records.')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    accounts = json.loads((ROOT / '.local' / 'demo-credentials.json').read_text(encoding='utf-8'))['accounts']
    report = {'checks': [], 'console_errors': [], 'page_errors': [], 'started_at': datetime.now().isoformat()}
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed, executable_path=args.browser_executable)
            read_checks(browser, accounts, report)
            if not args.read_only:
                workflow_check(browser, accounts, report)
            browser.close()
        assert not report['page_errors'], 'JavaScript runtime errors: ' + repr(report['page_errors'])
        assert not report['console_errors'], 'Browser errors: ' + repr(report['console_errors'])
        report['status'] = 'passed'
    except Exception as error:
        report['status'] = 'failed'
        report['failure'] = str(error)
        raise
    finally:
        report['finished_at'] = datetime.now().isoformat()
        (OUT / 'ui-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'checks': len(report['checks']), 'console_errors': len(report['console_errors']), 'page_errors': len(report['page_errors']), 'screenshots': len(list(OUT.glob('*.png')))}))


if __name__ == '__main__':
    main()
