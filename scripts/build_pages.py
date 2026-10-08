"""Build the public project report and Sphinx docs without a running database.

Only explicitly selected report assets are published. The real Git log is
read at build time; responsibility in the original plan is not Git authorship.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist" / "pages"


def history(directory: Path) -> list[dict]:
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(directory), "log", "-30", "--format=%H%x09%aI%x09%an%x09%s"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    if result.returncode:
        return []
    commits = []
    for line in result.stdout.splitlines():
        sha, date, author, subject = line.split("\t", 3)
        commits.append({"sha": sha, "date": date[:10], "author": author, "subject": subject})
    return commits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-dir", type=Path, default=ROOT, help="checkout to read actual Git history from")
    args = parser.parse_args()
    content = json.loads((ROOT / "report/content.json").read_text(encoding="utf-8"))
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if content["version"] != version:
        raise SystemExit("Report version must match pyproject.toml")
    for item in content["team"] + content["milestones"]:
        for filename in item["files"]:
            if not (ROOT / filename).is_file():
                raise SystemExit("Report source does not exist: " + filename)
    for task in content["tasks"]:
        if not (ROOT / task["file"]).is_file():
            raise SystemExit("Task source does not exist: " + task["file"])
    evidence = json.loads((ROOT / "docs/evidence/acceptance-summary.json").read_text(encoding="utf-8"))
    results = evidence["results"]
    content["metrics"] = {
        "p95": f'{results["T16"]["facts"]["p95_seconds"]:.3f}',
        "samples": results["T16"]["facts"]["measurements"],
        "users": results["T16"]["facts"]["concurrent_users"],
        "restore_tables": sum(db["table_count"] for db in results["T15"]["facts"]["databases"].values()),
        "passed": sum(row["status"] == "PASS" for row in results.values()),
        "total": len(results),
    }
    content["commits"] = history(args.git_dir.resolve())
    revision = content["commits"][0]["sha"] if content["commits"] else "main"
    content["revision"] = revision
    content["built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    content["owners"] = {member["id"]: member for member in content["team"]}
    content["owners"]["all"] = {"short": "Вся команда", "color": "green"}
    subprocess.run([sys.executable, "scripts/build_docs.py"], cwd=ROOT, check=True)

    # Refuse cleanup outside the one known generated directory, including symlinks.
    if OUTPUT.is_symlink() or OUTPUT.resolve() != ROOT.resolve() / "dist" / "pages":
        raise SystemExit("Unsafe Pages output path")
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    (OUTPUT / "assets").mkdir(parents=True)
    environment = Environment(
        loader=FileSystemLoader(ROOT / "report"), autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
    )
    environment.globals["source"] = lambda path: f'{content["repository"]}/blob/{revision}/{quote(path, safe="/")}'
    (OUTPUT / "index.html").write_text(environment.get_template("index.html").render(**content), encoding="utf-8")
    for name in ("report.css", "report.js", "favicon.svg"):
        shutil.copy2(ROOT / "report" / name, OUTPUT / "assets" / name)
    for screenshot in content["screenshots"]:
        shutil.copy2(ROOT / "docs/evidence" / screenshot["file"], OUTPUT / "assets" / screenshot["file"])
    shutil.copytree(ROOT / "docs/_build/html", OUTPUT / "docs")
    shutil.copy2(ROOT / "report/content.json", OUTPUT / "development.json")
    (OUTPUT / "build.json").write_text(json.dumps({
        "version": version, "revision": revision, "built_at": content["built_at"],
        "commits": content["commits"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT / ".nojekyll").touch()
    print(f"Pages: {OUTPUT} ({len(content['commits'])} real commits, version {version})")


if __name__ == "__main__":
    main()
