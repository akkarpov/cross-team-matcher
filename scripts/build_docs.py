"""Build Sphinx and optionally the laboratory's Doxygen HTML/RTF output."""
from pathlib import Path
import argparse
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


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
    subprocess.run([sys.executable, "-m", "sphinx", "-W", "--keep-going", "-b", "html", "docs", "docs/_build/html"], cwd=ROOT, env=env, check=True)
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
