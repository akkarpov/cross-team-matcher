"""Check the built report under a URL prefix, including real browser interactions."""
from __future__ import annotations

import argparse
from functools import partial
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "dist/pages"
EVIDENCE = ROOT / ".local/test-results/pages"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            assert values["id"] not in self.ids, f"Duplicate ID: {values['id']}"
            self.ids.add(values["id"])
        for name in ("href", "src"):
            if values.get(name):
                self.links.append(values[name])


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def check_links():
    checked = 0
    for path in (SITE / "index.html", *sorted((SITE / "docs").glob("*.html"))):
        document = Links()
        document.feed(path.read_text(encoding="utf-8"))
        for link in document.links:
            url = urlsplit(link)
            if url.scheme or url.netloc:
                continue
            assert not url.path.startswith("/"), f"Link breaks repository prefix: {link}"
            target = (path.parent / unquote(url.path)).resolve() if url.path else path
            assert target.is_relative_to(SITE.resolve()), f"Link outside published site: {link}"
            assert target.exists(), f"Missing target from {path.name}: {link}"
            if not url.path and url.fragment:
                assert unquote(url.fragment) in document.ids, f"Missing anchor: {path.name} {link}"
            checked += 1
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable", help="optional existing Chromium binary for local checks")
    args = parser.parse_args()
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    report = {"local_links": check_links(), "checks": [], "errors": []}
    handler = partial(QuietHandler, directory=str(ROOT / "dist"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/pages/"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser_executable)
            context = browser.new_context(viewport={"width": 1440, "height": 1050}, locale="ru-RU", reduced_motion="reduce")
            page = context.new_page()
            page.on("pageerror", lambda error: report["errors"].append(str(error)))
            page.on("console", lambda message: report["errors"].append(f"{message.text} {message.location}") if message.type == "error" else None)
            page.on("response", lambda response: report["errors"].append(f"HTTP {response.status}: {response.url}") if response.status >= 400 else None)
            response = page.goto(base, wait_until="networkidle")
            assert response.status == 200
            assert page.locator("h1").count() == 1
            assert page.locator(".team-card").count() == 3
            assert page.locator(".milestone:visible").count() == 7
            report["checks"].append("Report renders below a repository URL prefix")
            for owner, count in (("karpov", 4), ("lebedev", 5), ("krechun", 4), ("all", 7)):
                page.locator(f'[data-filter="{owner}"]').click()
                assert page.locator(".milestone:visible").count() == count
                assert page.locator(f'[data-filter="{owner}"]').get_attribute("aria-pressed") == "true"
            report["checks"].append("All team filters include shared stages and restore the full timeline")
            for flow in ("catalog", "replica", "command", "all"):
                page.locator(f'[data-flow="{flow}"]').click()
                assert page.locator(".flow-lane.dimmed").count() == (0 if flow == "all" else 2)
                assert page.locator("#flow-description").inner_text().strip()
            report["checks"].append("Each architecture flow changes highlighted routes and explanation")
            for index in range(6):
                page.locator(f'[data-step="{index}"]').click()
                assert page.locator("#step-counter").inner_text() == f"0{index + 1} / 06"
                assert page.locator("#step-title").inner_text().strip()
                assert page.locator("#step-guarantee").inner_text().strip()
            page.locator("#next-step").click()
            assert page.locator('[data-step="0"]').get_attribute("aria-pressed") == "true"
            report["checks"].append("All six invitation steps work; final step loops to the start")
            for index in range(3):
                trigger = page.locator(".screenshot-open").nth(index)
                trigger.click()
                assert page.locator("#image-dialog").is_visible()
                assert page.locator("#full-image").evaluate("image => image.complete && image.naturalWidth > 0")
                page.keyboard.press("Escape")
                assert not page.locator("#image-dialog").is_visible()
                assert trigger.evaluate("element => element === document.activeElement")
            report["checks"].append("Three screenshots open, Escape closes, keyboard focus returns")
            page.locator(".task-details summary").click()
            assert page.locator(".task-details tbody tr:visible").count() == 10
            page.locator(".task-details summary").click()
            report["checks"].append("Original ten-task table expands")
            for width in (1440, 768, 390, 320):
                page.set_viewport_size({"width": width, "height": 1050 if width > 760 else 844})
                page.evaluate("window.scrollTo(0, 0)")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), f"Overflow at {width}px"
                if width in (1440, 390):
                    page.screenshot(path=str(EVIDENCE / f"report-{width}-viewport.png"))
                    page.screenshot(path=str(EVIDENCE / f"report-{width}.png"), full_page=True)
                report["checks"].append(f"No horizontal overflow at {width}px")
            page.set_viewport_size({"width": 1440, "height": 1050})
            page.locator('[data-filter="karpov"]').click()
            page.emulate_media(media="print")
            page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
            assert page.locator(".milestone:visible").count() == 7
            assert page.locator(".task-details").get_attribute("open") is not None
            page.pdf(path=str(EVIDENCE / "report.pdf"), format="A4", print_background=True, margin={"top": "12mm", "bottom": "12mm", "left": "8mm", "right": "8mm"})
            report["checks"].append("PDF includes every stage and expanded tasks")
            page.emulate_media(media="screen")
            page.evaluate("window.dispatchEvent(new Event('afterprint'))")
            assert page.locator(".milestone:visible").count() == 4
            page.goto(base + "docs/development.html", wait_until="networkidle")
            assert "Команда, этапы и версии" in page.locator("div.body h1").inner_text()
            report["checks"].append("Published Sphinx development documentation opens")
            context.close()
            no_js = browser.new_context(java_script_enabled=False)
            static_page = no_js.new_page()
            static_page.goto(base, wait_until="networkidle")
            assert static_page.locator(".milestone:visible").count() == 7
            assert static_page.locator("#workflow noscript").is_visible()
            report["checks"].append("Report and workflow explanation remain readable without JavaScript")
            no_js.close()
            browser.close()
        assert not report["errors"], report["errors"]
        report["status"] = "PASS"
    finally:
        server.shutdown()
        server.server_close()
        (EVIDENCE / "checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
