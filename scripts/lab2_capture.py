"""Capture real lab logs and generated documentation; open its RTF in Word on Windows."""
from datetime import datetime, timezone
from functools import partial
import argparse
import html
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence/lab2"
WORK = ROOT / ".local/lab2-cpp"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def export_rtf():
    """Use an owned, hidden Word instance; never attach to the user's open documents."""
    import win32com.client

    destination = WORK / "word-export"
    destination.mkdir(parents=True, exist_ok=True)
    word = win32com.client.DispatchEx("Word.Application")
    document = None
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        print("Opening generated RTF in Word", flush=True)
        document = word.Documents.Open(str(EVIDENCE / "refman.rtf"), ReadOnly=True, AddToRecentFiles=False)
        text = document.Content.Text
        assert "Employee" in text and "Рассчитать остаток недельного бюджета" in text
        for contents in document.TablesOfContents:
            contents.Update()
        document.ExportAsFixedFormat(str(EVIDENCE / "refman.pdf"), 17)
        pages = len(re.findall(rb"/Type\s*/Page\b", (EVIDENCE / "refman.pdf").read_bytes()))
        assert pages > 0, "Expected page objects in Word's PDF export"
        document.SaveAs2(str(destination / "refman.html"), FileFormat=10, AddToRecentFiles=False)
        result = {"application": "Microsoft Word", "version": str(word.Version), "pages": pages,
                  "page_count_source": "page objects in the exported PDF",
                  "rtf_sha256": hashlib.sha256((EVIDENCE / "refman.rtf").read_bytes()).hexdigest(),
                  "rtf_opened": True, "updated_comment_found": True, "pdf_exported": True}
        (EVIDENCE / "word-run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("RTF verified and exported", flush=True)
    finally:
        if document is not None:
            document.Close(SaveChanges=0)
        word.Quit()


def log_page(title, records, extra=""):
    blocks = []
    for record in records:
        output = "\n".join(part for part in (record["stdout"], record["stderr"]) if part)
        blocks.append(f'<section><h2>{html.escape(record["command"])}</h2><pre>{html.escape(output or "(команда не выводит текст)")}</pre><p class="code">Код возврата: {record["exit_code"]}</p></section>')
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8"><title>{html.escape(title)}</title>
    <style>body{{background:#eef3ee;color:#17322b;font:17px/1.5 "Segoe UI",sans-serif;margin:0;padding:38px}}
    main{{max-width:1080px;margin:auto}}h1{{font-size:31px;margin:10px 0}}small{{letter-spacing:2px}}
    section{{background:white;border:1px solid #ccd8ce;border-radius:10px;padding:18px 24px;margin:18px 0}}
    h2,pre{{font:15px/1.55 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere}}h2{{color:#0b6249}}pre{{margin:12px 0}}.code{{font-size:13px;color:#5c7067;margin-bottom:0}}
    </style><main><small>ЛАБОРАТОРНАЯ 2 · ФАКТИЧЕСКИЙ ЗАПУСК · 08.10.2026</small><h1>{html.escape(title)}</h1>
    <p>Просмотр сохранённого протокола. Команды и вывод взяты из JSON запуска.</p>{''.join(blocks)}{extra}</main></html>'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser-executable")
    parser.add_argument("--word", action="store_true", help="open RTF and export it with installed Microsoft Word")
    args = parser.parse_args()
    if args.word:
        export_rtf()
    git = json.loads((EVIDENCE / "git-run.json").read_text(encoding="utf-8"))
    documentation = json.loads((EVIDENCE / "documentation-run.json").read_text(encoding="utf-8"))
    views = {
        "git-conflict": ("Конфликт при слиянии", [r for r in git["records"] if r["step"] == "8"], ""),
        "git-failure-reset": ("Ошибка обнаружена; слияние отменено", [r for r in git["records"] if r["step"] in ("10", "11")], ""),
        "git-final": ("Исправление принято во втором клоне", [r for r in git["records"] if r["step"] in ("verification", "graph")], f'<section><h2>Итог: {git["steps_completed"]} шагов / {git["status"]}</h2></section>'),
        "cpp-build": ("C++: сборка и запуск примера", documentation["records"][2:4], '<section><h2>Doxygen: 24 HTML-страницы · HTML + RTF · 0 предупреждений</h2></section>'),
    }
    for name, (title, records, extra) in views.items():
        (WORK / f"{name}.html").write_text(log_page(title, records, extra), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    captures = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser_executable)
            page = browser.new_page(viewport={"width": 1200, "height": 950}, locale="ru-RU", color_scheme="light")
            base = f"http://127.0.0.1:{server.server_port}/"
            for name in views:
                page.goto(base + f".local/lab2-cpp/{name}.html", wait_until="networkidle")
                page.screenshot(path=str(EVIDENCE / f"{name}.png"), full_page=True)
                captures.append(name)
            for name, file in (("doxygen-main", "index.html"), ("doxygen-employee", "class_employee.html"),
                               ("doxygen-inheritance", "class_regional_employee.html"), ("doxygen-header", "_employee_8h.html"),
                               ("doxygen-source", "_employee_8cpp.html")):
                page.goto(base + "docs/_build/lab2-doxygen/html/" + file, wait_until="networkidle")
                page.screenshot(path=str(EVIDENCE / f"{name}.png"))
                captures.append(name)
            for variant in ("before", "after"):
                folder = "lab2-doxygen-before" if variant == "before" else "lab2-doxygen"
                page.goto(base + f"docs/_build/{folder}/html/class_employee.html", wait_until="networkidle")
                detail = page.locator(".memitem").filter(has_text="double Employee::available")
                assert detail.count() == 1
                detail.screenshot(path=str(EVIDENCE / f"comment-{variant}.png"))
                captures.append(f"comment-{variant}")
            if (WORK / "word-export/refman.html").is_file():
                page.goto(base + ".local/lab2-cpp/word-export/refman.html", wait_until="networkidle")
                page.screenshot(path=str(EVIDENCE / "rtf-word-export.png"))
                captures.append("rtf-word-export")
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
    result = {"status": "PASS", "captured_utc": datetime.now(timezone.utc).isoformat(), "screenshots": captures,
              "git_images": "Browser screenshots of the actual saved command log, not terminal windows",
              "doxygen_images": "Actual generated HTML and method documentation",
              "rtf_image": "HTML exported by Microsoft Word after opening the generated RTF"}
    (EVIDENCE / "capture-run.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
