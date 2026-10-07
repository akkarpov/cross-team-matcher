"""Isolated three-node PostgreSQL laboratory lifecycle.

Runtime secrets, logs, process IDs and data stay in .local. This tool never uses
the system PostgreSQL service and never removes an existing data directory.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".local"
sys.path.insert(0, str(ROOT))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def load_config(path: str | Path | None = None) -> dict:
    """Read deployment metadata without printing secrets."""
    filename = Path(path) if path else RUNTIME / "config.json"
    if not filename.exists():
        raise SystemExit("No deployment configured. Run python scripts/local.py init first.")
    return json.loads(filename.read_text(encoding="utf-8"))


def dsn(config: dict, region: int, role: str = "postgres", database: str = "matcher") -> str:
    node = config["nodes"][region]
    timeout = 15 if role in ("postgres", "matcher_owner") else 3
    return (f"postgresql://{role}:{quote(config['passwords'][role], safe='')}@"
            f"{node['host']}:{node['port']}/{database}?connect_timeout={timeout}")


def run(command: list[str], *, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=ROOT, env=env, check=check, text=True,
                          encoding="utf-8", errors="replace", creationflags=NO_WINDOW)


def pg_bin(config: dict, program: str) -> str:
    folder = config.get("pg_bin")
    return str(Path(folder) / (program + ".exe")) if folder else program


def initialize(args: argparse.Namespace) -> None:
    RUNTIME.mkdir(exist_ok=True)
    config_path = RUNTIME / "config.json"
    if config_path.exists():
        configuration = load_config(config_path)
        if not configuration.get("pg_bin"):
            binary_dir = Path(args.pg_bin)
            if not (binary_dir / "initdb.exe").exists():
                raise SystemExit(f"PostgreSQL 17 initdb not found in {binary_dir}")
            configuration["pg_bin"] = str(binary_dir)
            config_path.write_text(json.dumps(configuration, indent=2), encoding="utf-8")
        initialize_clusters(configuration)
        print("Existing configuration retained; use db-start and setup to resume.")
        return
    binary_dir = Path(args.pg_bin)
    if not (binary_dir / "initdb.exe").exists():
        raise SystemExit(f"PostgreSQL 17 initdb not found in {binary_dir}")
    config = new_configuration(str(binary_dir))
    for node in config["nodes"]:
        with socket.socket() as test_socket:
            try:
                test_socket.bind((node["host"], node["port"]))
            except OSError as error:
                raise SystemExit(f"Port {node['port']} unavailable: {error}") from error
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    initialize_clusters(config)


def new_configuration(binary_dir: str | None = None) -> dict:
    """Generate deployment secrets; usable without local PostgreSQL for Compose."""
    roles = ["postgres", "matcher_owner", "matcher_app", "matcher_worker", "matcher_repl",
             "transport_c", "transport_r1", "transport_r2"]
    return {"version": 1, "pg_bin": binary_dir,
            "passwords": {role: secrets.token_urlsafe(32) for role in roles},
            "session_secrets": [secrets.token_urlsafe(48) for _ in range(3)],
            "nodes": [{"region": r, "host": "127.0.0.1", "port": 55430 + r,
                       "app_port": 8100 + r, "data": str(RUNTIME / f"pg{r}")}
                      for r in range(3)]}


def initialize_clusters(config: dict) -> None:
    password_file = RUNTIME / "initdb-password.txt"
    password_file.write_text(config["passwords"]["postgres"], encoding="utf-8")
    try:
        for node in config["nodes"]:
            data = Path(node["data"]).resolve()
            if not data.is_relative_to(RUNTIME.resolve()):
                raise SystemExit("Refusing cluster path outside this workspace .local directory")
            if (data / "PG_VERSION").exists():
                installed = (data / "postgresql.conf").read_text(encoding="utf-8", errors="replace")
                if "# Cross-Team Matcher isolated laboratory" not in installed or not (data / "global" / "pg_control").exists():
                    raise SystemExit(f"Incomplete initialization at {data}. Preserve/rename this directory after checking no initdb/postgres process uses it; init never overwrites an existing cluster.")
                continue
            run([pg_bin(config, "initdb"), "-D", str(data), "-U", "postgres",
                 "--pwfile", str(password_file), "--auth=scram-sha-256", "--encoding=UTF8",
                 "--locale=C"])
            with (data / "postgresql.conf").open("a", encoding="utf-8") as output:
                output.write(f"\n# Cross-Team Matcher isolated laboratory\nport = {node['port']}\n"
                             "listen_addresses = '127.0.0.1'\nwal_level = logical\n"
                             "max_replication_slots = 20\nmax_wal_senders = 20\n"
                             "max_logical_replication_workers = 12\nmax_worker_processes = 24\n"
                             "shared_buffers = '64MB'\nmax_connections = 60\n"
                             "log_min_messages = warning\nmax_slot_wal_keep_size = '1GB'\n")
        print("Initialized 3 isolated clusters. Runtime secrets: .local/config.json")
    finally:
        password_file.unlink(missing_ok=True)


def database_control(config: dict, action: str, regions: list[int] | None = None) -> None:
    for region in regions if regions is not None else range(3):
        node = config["nodes"][region]
        data = Path(node["data"]).resolve()
        if not data.is_relative_to(RUNTIME.resolve()) or not (data / "PG_VERSION").exists():
            raise SystemExit("Refusing control of a PostgreSQL cluster outside initialized .local")
        status = subprocess.run([pg_bin(config, "pg_ctl"), "-D", str(data), "status"],
                                capture_output=True, creationflags=NO_WINDOW)
        if action == "start" and status.returncode != 0:
            run([pg_bin(config, "pg_ctl"), "-D", str(data), "-l",
                 str(RUNTIME / f"postgres-{region}.log"), "-w", "start"])
        elif action == "stop" and status.returncode == 0:
            run([pg_bin(config, "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"])
        else:
            print(f"Node {region}: already {'running' if status.returncode == 0 else 'stopped'}")


def app_environment(config: dict, region: int, worker: bool = False) -> dict:
    env = os.environ.copy()
    env.update(DATABASE_URL=dsn(config, region, "matcher_worker" if worker else "matcher_app"),
               NODE_REGION=str(region), SESSION_SECRET=config["session_secrets"][region],
               PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    return env


def applications_start(config: dict) -> None:
    processes_path = RUNTIME / "processes.json"
    if processes_path.exists():
        raise SystemExit("Application process file exists. Run stop before starting again.")
    processes = []
    try:
        for node in config["nodes"]:
            region = node["region"]
            for worker in (False, True):
                label = "worker" if worker else "app"
                command = ([sys.executable, "-m", "app.worker"] if worker else
                           [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                            "--port", str(node["app_port"])])
                with (RUNTIME / f"{label}-{region}.log").open("a", encoding="utf-8") as log:
                    process = subprocess.Popen(command, cwd=ROOT, env=app_environment(config, region, worker),
                                               stdout=log, stderr=subprocess.STDOUT,
                                               creationflags=NO_WINDOW)
                processes.append({"pid": process.pid, "region": region, "kind": label,
                                  "created_at": time.time()})
        processes_path.write_text(json.dumps(processes, indent=2), encoding="utf-8")
        time.sleep(2)
        print("Apps: http://127.0.0.1:8100, http://127.0.0.1:8101, http://127.0.0.1:8102")
        print("Logs: .local/app-*.log and .local/worker-*.log")
    except Exception:
        processes_path.write_text(json.dumps(processes, indent=2), encoding="utf-8")
        raise


def applications_stop() -> None:
    path = RUNTIME / "processes.json"
    if not path.exists():
        return
    import psutil
    for info in json.loads(path.read_text(encoding="utf-8")):
        try:
            process = psutil.Process(info["pid"])
            command = process.cmdline()
            # Check identity before touching a PID that might have been reused.
            if (Path(process.cwd()).resolve() == ROOT and
                abs(process.create_time() - info["created_at"]) < 15 and
                ("app.worker" in command or "app.main:app" in command)):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except psutil.TimeoutExpired:
                    process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "db-start", "db-stop", "setup", "seed", "start", "stop", "status", "backup", "restore", "compose-env"])
    parser.add_argument("--config")
    parser.add_argument("--pg-bin", default=r"C:\Program Files\PostgreSQL\17\bin")
    parser.add_argument("--region", type=int, choices=[0, 1, 2])
    parser.add_argument("--backup")
    args = parser.parse_args()
    if args.command == "init":
        initialize(args)
        return
    if args.command == "compose-env" and not (RUNTIME / "config.json").exists():
        RUNTIME.mkdir(exist_ok=True)
        (RUNTIME / "config.json").write_text(json.dumps(new_configuration(), indent=2), encoding="utf-8")
    config = load_config(args.config)
    if args.command in ("db-start", "db-stop"):
        database_control(config, args.command[3:], [args.region] if args.region is not None else None)
    elif args.command == "setup":
        from replication.bootstrap import setup
        setup(config)
    elif args.command == "seed":
        from scripts.seed import seed
        seed(config)
    elif args.command == "start":
        database_control(config, "start")
        applications_start(config)
    elif args.command == "stop":
        applications_stop()
        database_control(config, "stop")
    elif args.command == "status":
        import psycopg
        for region in range(3):
            try:
                with psycopg.connect(dsn(config, region)) as conn:
                    version = conn.execute("SELECT version()").fetchone()[0]
                    subscriptions = conn.execute("SELECT subname, subenabled FROM pg_subscription").fetchall()
                print(json.dumps({"region": region, "version": version, "subscriptions": subscriptions}))
            except psycopg.Error as error:
                print(f"Node {region}: {error.__class__.__name__}")
    elif args.command in ("backup", "restore"):
        from scripts.backup import backup, restore
        (backup(config) if args.command == "backup" else restore(config, args.backup))
    elif args.command == "compose-env":
        from replication.bootstrap import compose_environment
        compose_environment(config)


if __name__ == "__main__":
    main()
