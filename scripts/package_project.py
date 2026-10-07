"""Create a source-and-documentation handoff archive without runtime secrets."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / "cross-team-matcher-4.1.zip"
    folders = ["app", "contracts", "deploy", "design", "docs", "labs", "replication", "report", "scripts", "sql", "tests", ".github"]
    files = ["README.md", "CHANGELOG.md", ".gitignore", "requirements.txt", "requirements-dev.txt", "pyproject.toml", "Makefile", "Doxyfile"]
    paths = [ROOT / name for name in files if (ROOT / name).exists()]
    for folder in folders:
        if (ROOT / folder).exists():
            paths.extend(p for p in (ROOT / folder).rglob("*") if p.is_file())
    manifest = []
    secrets = []
    configuration = ROOT / ".local" / "config.json"
    if configuration.exists():
        runtime = json.loads(configuration.read_text(encoding="utf-8"))
        secrets.extend(runtime.get("passwords", {}).values())
        secrets.extend(runtime.get("session_secrets", []))
    credentials = ROOT / ".local" / "demo-credentials.json"
    if credentials.exists():
        secrets.extend(item["password"] for item in json.loads(credentials.read_text(encoding="utf-8"))["accounts"])
    secret_bytes = [value.encode() for value in secrets if isinstance(value, str) and len(value) >= 12]
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(set(paths)):
            relative = path.relative_to(ROOT)
            if any(part in {"__pycache__", ".pytest_cache", ".doctrees", ".local", ".venv", "node_modules"} for part in relative.parts):
                continue
            if path.name == ".env" or path.suffix in {".pyc", ".dump", ".log"}:
                continue
            if path.is_symlink():
                continue
            content = path.read_bytes()
            if any(value in content for value in secret_bytes):
                raise SystemExit("Refusing to package a runtime secret in " + relative.as_posix())
            bundle.writestr(str(Path("cross-team-matcher") / relative).replace("\\", "/"), content)
            manifest.append({"path": relative.as_posix(), "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (destination / "manifest.json").write_text(json.dumps({"archive": archive.name, "sha256": checksum, "files": manifest}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Packaged {len(manifest)} files, {archive.stat().st_size:,} bytes: {archive.relative_to(ROOT)}")
    print("SHA256 " + checksum)


if __name__ == "__main__":
    main()
