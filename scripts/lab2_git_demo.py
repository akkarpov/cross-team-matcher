"""Reproduce Git lab in isolated local repositories, preserving project history."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    base = ROOT / ".local" / "lab2-git" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    if not base.resolve().is_relative_to((ROOT / ".local").resolve()):
        raise RuntimeError("Lab directory escaped workspace")
    base.mkdir(parents=True)
    transcript = ["Lab 2: real Git operations; local bare remote; synthetic identities.", ""]
    def run(cwd, *args, expected=0):
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        transcript.extend(["> " + " ".join(str(a) for a in args), result.stdout.strip(), result.stderr.strip(), f"exit={result.returncode}", ""])
        if result.returncode != expected:
            (base / "failed-transcript.txt").write_text("\n".join(transcript), encoding="utf-8")
            raise RuntimeError("Unexpected command result: " + " ".join(str(a) for a in args) + "\n" + result.stderr)
        return result.stdout.strip()
    run(base, "git", "init", "--bare", "--initial-branch=main", "origin.git")
    run(base, "git", "clone", str(base / "origin.git"), "author")
    author = base / "author"
    run(author, "git", "config", "user.name", "Lab Participant")
    run(author, "git", "config", "user.email", "lab@example.invalid")
    good = "def remaining(capacity, assigned):\n    return capacity - assigned\n"
    bad = "def remaining(capacity, assigned):\n    return capacity + assigned\n"
    (author / "budget.py").write_text(good, encoding="utf-8")
    (author / "test_budget.py").write_text("from budget import remaining\nassert remaining(40, 24) == 16\n", encoding="utf-8")
    run(author, "git", "add", ".")
    run(author, "git", "commit", "-m", "Initial tested capacity model")
    run(author, "git", "push", "-u", "origin", "main")
    run(base, "git", "clone", str(base / "origin.git"), "reviewer")
    reviewer = base / "reviewer"
    run(reviewer, "git", "pull", "--ff-only")
    run(reviewer, "git", "log", "--oneline", "--all")
    run(author, "git", "switch", "-c", "feature/calendar")
    (author / "budget.py").write_text(bad, encoding="utf-8")
    run(author, "git", "add", "budget.py")
    run(author, "git", "commit", "-m", "Feature with a deliberate teaching defect")
    run(author, "git", "push", "-u", "origin", "feature/calendar")
    run(author, "git", "switch", "main")
    (author / "budget.py").write_text("def remaining(capacity, assigned):\n    return max(0, capacity - assigned)\n", encoding="utf-8")
    run(author, "git", "add", "budget.py")
    run(author, "git", "commit", "-m", "Concurrent main change")
    run(author, "git", "merge", "--no-ff", "feature/calendar", expected=1)
    (author / "budget.py").write_text(bad, encoding="utf-8")
    run(author, "git", "add", "budget.py")
    run(author, "git", "commit", "-m", "Resolve conflict incorrectly for the lab")
    bad_merge = run(author, "git", "rev-parse", "HEAD")
    run(author, sys.executable, "-B", "test_budget.py", expected=1)
    run(author, "git", "revert", "-m", "1", "--no-edit", bad_merge)
    run(author, sys.executable, "-B", "test_budget.py")
    run(author, "git", "switch", "feature/calendar")
    run(author, "git", "branch", "-m", "feature/calendar-fixed")
    (author / "budget.py").write_text(good, encoding="utf-8")
    run(author, "git", "add", "budget.py")
    run(author, "git", "commit", "-m", "Fix capacity subtraction")
    run(author, sys.executable, "-B", "test_budget.py")
    run(author, "git", "switch", "main")
    merge = subprocess.run(["git", "merge", "--no-ff", "feature/calendar-fixed", "-m", "Integrate corrected feature"], cwd=author, capture_output=True, text=True, encoding="utf-8", errors="replace")
    transcript.extend(["> git merge --no-ff feature/calendar-fixed", merge.stdout, merge.stderr, f"exit={merge.returncode}"])
    if merge.returncode == 1:
        (author / "budget.py").write_text(good, encoding="utf-8")
        run(author, "git", "add", "budget.py")
        run(author, "git", "commit", "-m", "Resolve second conflict with tested subtraction")
    elif merge.returncode != 0:
        raise RuntimeError("Merge failed unexpectedly")
    run(author, sys.executable, "-B", "test_budget.py")
    run(author, "git", "push", "origin", "main")
    run(author, "git", "branch", "--unset-upstream", "feature/calendar-fixed")
    run(author, "git", "branch", "-d", "feature/calendar-fixed")
    run(author, "git", "push", "origin", "--delete", "feature/calendar")
    run(reviewer, "git", "pull", "--ff-only")
    run(reviewer, sys.executable, "-B", "test_budget.py")
    run(reviewer, "git", "log", "--oneline", "--graph", "--all")
    path = ROOT / "docs" / "generated" / "git-transcript.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(transcript), encoding="utf-8")
    print("Git lab passed; transcript: " + str(path.relative_to(ROOT)))


if __name__ == "__main__":
    main()
