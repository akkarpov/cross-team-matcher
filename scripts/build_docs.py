"""Build Sphinx and optionally the laboratory's Doxygen HTML/RTF output."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def publish_lab_reference(output):
    """Publish the verified lab artifact with a local favicon under any URL prefix."""
    reference = output / "lab2-reference"
    with zipfile.ZipFile(ROOT / "docs/evidence/lab2/doxygen-html.zip") as archive:
        for item in archive.infolist():
            if not (reference / item.filename).resolve().is_relative_to(reference.resolve()):
                raise SystemExit("Unsafe path in Doxygen artifact")
        archive.extractall(reference)
    shutil.copy2(ROOT / "report/favicon.svg", reference / "favicon.svg")
    for page in reference.rglob("*.html"):
        favicon = os.path.relpath(reference / "favicon.svg", page.parent).replace(os.sep, "/")
        source = page.read_text(encoding="utf-8")
        page.write_text(source.replace("</head>", f'<link rel="icon" type="image/svg+xml" href="{favicon}">\n</head>', 1), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doxygen", action="store_true", help="also build real Doxygen HTML and RTF")
    parser.add_argument("--schema", action="store_true", help="export schema from the local running stand first")
    args = parser.parse_args()
    env = os.environ.copy()
    development = ROOT / ".work" / "dev-packages"
    if development.is_dir():
        env["PYTHONPATH"] = str(development) + os.pathsep + str(ROOT)
    if args.schema:
        subprocess.run([sys.executable, "docs/export_schema.py"], cwd=ROOT, env=env, check=True)
    output = ROOT / "docs/_build/html"
    if output.is_symlink() or output.resolve() != ROOT.resolve() / "docs/_build/html":
        raise SystemExit("Unsafe documentation output path")
    if output.exists():
        shutil.rmtree(output)
    subprocess.run([sys.executable, "-m", "sphinx", "-W", "--keep-going", "-b", "html", "docs", "docs/_build/html"], cwd=ROOT, env=env, check=True)
    # Publish the verified lab artifact without requiring a C++ toolchain in Pages CI.
    publish_lab_reference(output)
    if args.doxygen:
        binary = shutil.which("doxygen")
        if not binary:
            matches = list((ROOT / ".work" / "tools" / "doxygen").rglob("doxygen.exe"))
            binary = str(matches[0]) if matches else None
        if not binary:
            raise SystemExit("Install Doxygen or extract its official Windows ZIP into .work/tools/doxygen")
        subprocess.run([binary, "Doxyfile"], cwd=ROOT, check=True)
        for path in ("docs/_build/doxygen/html/index.html", "docs/_build/doxygen/rtf/refman.rtf"):
            if not (ROOT / path).is_file():
                raise SystemExit("Missing generated artifact: " + path)
    print("Documentation build completed.")


if __name__ == "__main__":
    main()
