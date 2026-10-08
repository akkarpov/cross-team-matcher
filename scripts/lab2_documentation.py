"""Build and execute the C++ lab, generate before/after Doxygen evidence."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = ROOT / "docs/evidence/lab2"
    destination.mkdir(parents=True, exist_ok=True)
    build = ROOT / ".local/lab2-cpp"
    build.mkdir(parents=True, exist_ok=True)
    records = []

    def run(args):
        result = subprocess.run([str(arg) for arg in args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        records.append({"command": " ".join(str(arg).replace(str(ROOT), "<project>") for arg in args),
                        "stdout": result.stdout.strip(), "stderr": result.stderr.strip(), "exit_code": result.returncode})
        if result.returncode:
            raise RuntimeError(result.stdout + result.stderr)
        return result.stdout.strip()

    compiler = shutil.which("g++")
    doxygen = shutil.which("doxygen")
    if not doxygen:
        matches = list((ROOT / ".work/tools/doxygen").rglob("doxygen.exe"))
        doxygen = str(matches[0]) if matches else None
    if not compiler or not doxygen:
        raise SystemExit("C++ compiler and Doxygen are required to execute the lab")
    compiler_version = run([compiler, "--version"]).splitlines()[0]
    doxygen_version = run([doxygen, "--version"])
    executable = build / "CrossTeamLab2.exe"
    run([compiler, "-std=c++17", "-Wall", "-Wextra", "-pedantic", ROOT / "labs/lab2/Employee.cpp", ROOT / "labs/lab2/main.cpp", "-o", executable])
    output = run([executable])
    assert "checks passed" in output

    before = build / "before"
    before.mkdir(exist_ok=True)
    for name in ("Employee.cpp", "main.cpp", "mainpage.dox"):
        shutil.copy2(ROOT / "labs/lab2" / name, before / name)
    shutil.copy2(destination / "Employee-before.h.txt", before / "Employee.h")
    configuration = (ROOT / "labs/lab2/Doxyfile").read_text(encoding="utf-8")
    baseline_config = build / "Doxyfile-before"
    baseline_config.write_text(configuration + f'\nINPUT = "{before.as_posix()}"\nOUTPUT_DIRECTORY = docs/_build/lab2-doxygen-before\nWARN_LOGFILE = docs/_build/lab2-before-warnings.log\n', encoding="utf-8")
    run([doxygen, baseline_config])
    run([doxygen, ROOT / "labs/lab2/Doxyfile"])
    generated = ROOT / "docs/_build/lab2-doxygen"
    old_html = (ROOT / "docs/_build/lab2-doxygen-before/html/class_employee.html").read_text(encoding="utf-8")
    new_html = (generated / "html/class_employee.html").read_text(encoding="utf-8")
    assert "Получить свободные часы" in old_html
    assert "Рассчитать остаток недельного бюджета" in new_html
    assert "Метод не изменяет состояние сотрудника" in new_html
    warnings = (generated / "warnings.log").read_text(encoding="utf-8")
    assert not warnings.strip(), warnings
    shutil.copy2(generated / "rtf/refman.rtf", destination / "refman.rtf")
    with zipfile.ZipFile(destination / "doxygen-html.zip", "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((generated / "html").rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(generated / "html").as_posix())
    summary = {"status": "PASS", "executed_utc": datetime.now(timezone.utc).isoformat(),
               "compiler": compiler_version, "doxygen": doxygen_version, "cpp_assertions": 3,
               "html_files": len(list((generated / "html").rglob("*.html"))), "warnings": 0,
               "rtf_bytes": (destination / "refman.rtf").stat().st_size,
               "before_header_sha256": hashlib.sha256((destination / "Employee-before.h.txt").read_bytes()).hexdigest(),
               "after_header_sha256": hashlib.sha256((ROOT / "labs/lab2/Employee.h").read_bytes()).hexdigest(),
               "comment_update_verified": True, "records": records}
    (destination / "documentation-run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "records"}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
