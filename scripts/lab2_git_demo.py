"""Execute all 19 Git lab steps in new, isolated repositories and save evidence."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    started = datetime.now(timezone.utc)
    base = ROOT / ".local/lab2-git" / started.strftime("%Y%m%dT%H%M%S%f")
    assert base.resolve().is_relative_to((ROOT / ".local/lab2-git").resolve())
    base.mkdir(parents=True, exist_ok=False)
    author = base / "author"
    reviewer = base / "reviewer"
    evidence = ROOT / "docs/evidence/lab2"
    evidence.mkdir(parents=True, exist_ok=True)
    records = []

    def run(cwd, *args, step="setup", expected=0):
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        display = " ".join("python" if str(arg) == sys.executable else str(arg).replace(str(base), "<lab>") for arg in args)
        record = {"step": str(step), "cwd": cwd.relative_to(base).as_posix(), "command": display,
                  "stdout": result.stdout.strip().replace(str(base), "<lab>"),
                  "stderr": result.stderr.strip().replace(str(base), "<lab>"), "exit_code": result.returncode}
        records.append(record)
        if result.returncode != expected:
            (base / "failed-run.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(f"Unexpected exit code for {display}: {result.returncode}\n{result.stderr}")
        return result.stdout.strip()

    git_version = run(base, "git", "--version")
    run(base, "git", "init", "--bare", "--initial-branch=main", "origin.git")
    run(base, "git", "clone", str(base / "origin.git"), "author", step=1)
    run(author, "git", "config", "user.name", "Lab Participant")
    run(author, "git", "config", "user.email", "lab@example.invalid")
    (author / "budget.py").write_text("def remaining(capacity, assigned):\n    return capacity - assigned\n", encoding="utf-8")
    (author / "test_budget.py").write_text("from budget import remaining\nassert remaining(40, 24) == 16\nprint('PASS: remaining(40, 24) == 16')\n", encoding="utf-8")
    run(author, "git", "add", ".")
    run(author, "git", "commit", "-m", "Initial capacity model")
    (author / "README.txt").write_text("Lab 2: isolated capacity model, not the production repository.\n", encoding="utf-8")
    run(author, "git", "add", "README.txt")
    run(author, "git", "commit", "-m", "Document the teaching example")
    run(author, "git", "push", "-u", "origin", "main")
    run(base, "git", "clone", str(base / "origin.git"), "reviewer", step=1)
    run(author, "git", "pull", "--ff-only", step=2)
    run(author, "git", "diff", "HEAD^", step=3)
    run(author, "git", "checkout", "-b", "bad-feature", step=4)
    bad = "def remaining(capacity, assigned):\n    return capacity + assigned\n"
    (author / "budget.py").write_text(bad, encoding="utf-8")
    run(author, "git", "commit", "-am", "Introduce the deliberate capacity defect", step=5)
    run(author, "git", "push", "-u", "origin", "bad-feature", step=5)
    run(author, "git", "checkout", "main", step=6)
    run(author, "git", "pull", "--ff-only", step=7)
    corrected = "def remaining(capacity, assigned):\n    return max(0, capacity - assigned)\n"
    (author / "budget.py").write_text(corrected, encoding="utf-8")
    run(author, "git", "commit", "-am", "Clamp negative capacity in main", step=7)
    before_merge = run(author, "git", "rev-parse", "HEAD", step=7)
    run(author, "git", "merge", "--no-ff", "bad-feature", step=8, expected=1)
    conflict = (author / "budget.py").read_text(encoding="utf-8")
    assert "<<<<<<< HEAD" in conflict and ">>>>>>> bad-feature" in conflict
    run(author, "git", "status", "--short", step=8)
    run(author, "git", "diff", step=8)
    (author / "budget.py").write_text(bad, encoding="utf-8")
    run(author, "git", "add", "budget.py", step=9)
    run(author, "git", "commit", "-m", "Resolve conflict incorrectly for demonstration", step=9)
    bad_merge = run(author, "git", "rev-parse", "HEAD", step=9)
    run(author, "git", "diff", "HEAD^", step=10)
    run(author, sys.executable, "-B", "test_budget.py", step=10, expected=1)
    run(author, "git", "tag", "lab-bad-merge", bad_merge, step=10)
    # Only this newly created clone may be reset. Never the project checkout.
    if author.resolve() != base.resolve() / "author" or not (author / ".git").is_dir() or author.resolve() == ROOT.resolve():
        raise RuntimeError("Refusing reset outside the newly created lab clone")
    assert run(author, "git", "rev-parse", "ORIG_HEAD", step=11) == before_merge
    run(author, "git", "reset", "--hard", "ORIG_HEAD", step=11)
    after_reset = run(author, "git", "rev-parse", "HEAD", step=11)
    assert after_reset == before_merge
    run(author, sys.executable, "-B", "test_budget.py", step=11)
    run(author, "git", "checkout", "bad-feature", step=12)
    (author / "budget.py").write_text(corrected, encoding="utf-8")
    (author / "test_budget.py").write_text("from budget import remaining\nassert remaining(40, 24) == 16\nassert remaining(40, 48) == 0\nprint('PASS: normal capacity and overload checks')\n", encoding="utf-8")
    run(author, "git", "branch", "-m", "bad-feature", "good-feature", step=13)
    run(author, "git", "commit", "-am", "Correct subtraction and cover overload", step=14)
    run(author, sys.executable, "-B", "test_budget.py", step=14)
    run(author, "git", "checkout", "main", step=15)
    run(author, "git", "pull", "--ff-only", step=16)
    run(author, "git", "merge", "--no-ff", "good-feature", "-m", "Merge the tested capacity feature", step=17)
    run(author, sys.executable, "-B", "test_budget.py", step=17)
    run(author, "git", "push", "origin", "main", step=18)
    run(author, "git", "branch", "--unset-upstream", "good-feature", step=19)
    run(author, "git", "branch", "-d", "good-feature", step=19)
    run(author, "git", "push", "origin", "--delete", "bad-feature", step=19)
    run(reviewer, "git", "pull", "--ff-only", step="verification")
    run(reviewer, sys.executable, "-B", "test_budget.py", step="verification")
    final_head = run(reviewer, "git", "rev-parse", "HEAD", step="verification")
    graph = run(author, "git", "log", "--oneline", "--graph", "--all", "--decorate", step="graph")
    branches = run(author, "git", "branch", "-a", step="verification")
    assert "good-feature" not in branches and "bad-feature" not in branches
    assert {str(i) for i in range(1, 20)} <= {record["step"] for record in records}
    summary = {"status": "PASS", "started_utc": started.isoformat(), "finished_utc": datetime.now(timezone.utc).isoformat(),
               "git_version": git_version, "scope": "isolated local bare remote + two clones; synthetic lab identity",
               "steps_completed": 19, "before_merge": before_merge, "bad_merge": bad_merge,
               "after_reset": after_reset, "final_head": final_head, "graph": graph, "records": records}
    (evidence / "git-run.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    transcript = "Lab 2 — actual execution. Machine-specific paths shortened to <lab>.\n\n" + "\n\n".join(
        f"STEP {item['step']} / {item['cwd']}\n> {item['command']}\n{item['stdout']}\n{item['stderr']}\nexit={item['exit_code']}" for item in records)
    (ROOT / "docs/generated/git-transcript.txt").write_text("\n".join(line.rstrip() for line in transcript.splitlines()) + "\n", encoding="utf-8")
    (evidence / "conflict.txt").write_text(conflict, encoding="utf-8")
    print(json.dumps({key: summary[key] for key in ("status", "steps_completed", "before_merge", "bad_merge", "after_reset", "final_head")}, indent=2))


if __name__ == "__main__":
    main()
