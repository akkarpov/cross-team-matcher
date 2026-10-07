"""Install versioned SQL and the FDW/logical-replication topology.

The application knows only one local DSN. This deployment module is the only
place where addresses of the other PostgreSQL instances are assembled.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import psycopg
from psycopg import sql

from scripts.local import ROOT, RUNTIME, NO_WINDOW, dsn, pg_bin

WORKING_TABLES = ("employees", "employee_competencies", "capacity_periods",
                  "unavailability_periods", "projects", "project_positions",
                  "position_competencies", "invitations", "invitation_responses",
                  "assignments", "workflow_events", "reviews")
GLOBAL_TABLES = (*WORKING_TABLES, "processed_commands")


def install_roles(config: dict, region: int) -> None:
    with psycopg.connect(dsn(config, region, database="postgres"), autocommit=True) as conn:
        for role, password in config["passwords"].items():
            if role == "postgres":
                continue
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            if not exists:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(role)))
            conn.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            if role == "matcher_repl":
                conn.execute("ALTER ROLE matcher_repl REPLICATION")
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname='matcher'").fetchone():
            conn.execute("CREATE DATABASE matcher ENCODING 'UTF8' TEMPLATE template0")


def apply_migrations(config: dict, region: int) -> None:
    with psycopg.connect(dsn(config, region), autocommit=True) as conn:
        comment = conn.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0]
        applied = json.loads(comment[len("matcher:"):]) if comment and comment.startswith("matcher:") else {}
        for filename in sorted((ROOT / "sql").glob("[0-9][0-9]_*.sql")):
            digest = hashlib.sha256(filename.read_bytes()).hexdigest()
            if filename.name in applied:
                if applied[filename.name] != digest:
                    raise RuntimeError(f"Migration {filename.name} changed after installation on node {region}; add a new migration")
                continue
            node = config["nodes"][region]
            env = dict(os.environ, PGPASSWORD=config["passwords"]["postgres"], PGCLIENTENCODING="UTF8")
            result = subprocess.run([pg_bin(config, "psql"), "-X", "-h", node["host"], "-p", str(node["port"]),
                                     "-U", "postgres", "-d", "matcher", "-v", "ON_ERROR_STOP=1", "-v",
                                     f"node_region={region}", "--single-transaction", "-f", str(filename)],
                                    cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", creationflags=NO_WINDOW)
            if result.returncode:
                raise RuntimeError(f"Migration {filename.name} node {region}:\n{result.stderr}")
            applied[filename.name] = digest
            conn.execute(sql.SQL("COMMENT ON DATABASE matcher IS {}").format(sql.Literal("matcher:" + json.dumps(applied))))
            print(f"Node {region}: applied {filename.name}")


def configure_fdw(config: dict, region: int) -> None:
    source_role = ["transport_c", "transport_r1", "transport_r2"][region]
    with psycopg.connect(dsn(config, region), autocommit=True) as conn:
        for target in (1, 2):
            if target == region:
                continue
            node = config["nodes"][target]
            server = f"region{target}"
            if not conn.execute("SELECT 1 FROM pg_foreign_server WHERE srvname=%s", (server,)).fetchone():
                conn.execute(sql.SQL("CREATE SERVER {} FOREIGN DATA WRAPPER postgres_fdw OPTIONS (host {}, port {}, dbname 'matcher', connect_timeout '3')")
                             .format(sql.Identifier(server), sql.Literal(node["host"]), sql.Literal(str(node["port"]))))
            for local_role in ("postgres", "matcher_owner", "matcher_app", "matcher_worker"):
                remote_role = "matcher_owner" if local_role == "matcher_owner" else source_role
                conn.execute(sql.SQL("DROP USER MAPPING IF EXISTS FOR {} SERVER {}").format(sql.Identifier(local_role), sql.Identifier(server)))
                conn.execute(sql.SQL("CREATE USER MAPPING FOR {} SERVER {} OPTIONS (user {}, password {})")
                             .format(sql.Identifier(local_role), sql.Identifier(server), sql.Literal(remote_role), sql.Literal(config["passwords"][remote_role])))
            conn.execute(sql.SQL("GRANT USAGE ON FOREIGN SERVER {} TO matcher_app,matcher_worker,matcher_owner").format(sql.Identifier(server)))
            for table in GLOBAL_TABLES:
                foreign_name = f"{table}_r{target}"
                if not conn.execute("SELECT to_regclass(%s)", (f"route.{foreign_name}",)).fetchone()[0]:
                    conn.execute(sql.SQL("CREATE FOREIGN TABLE route.{} PARTITION OF global.{} FOR VALUES IN ({}) SERVER {} OPTIONS (schema_name {}, table_name {})")
                                 .format(sql.Identifier(foreign_name), sql.Identifier(table), sql.Literal(target),
                                         sql.Identifier(server), sql.Literal(f"r{target}_data"), sql.Literal(table)))
        for schema in ("global", "route", "r1_data", "r2_data", "cat"):
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO matcher_owner,matcher_repl").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO matcher_repl").format(sql.Identifier(schema)))
        conn.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA global,route TO matcher_owner")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA r1_data,r2_data,cat TO matcher_owner")
        if region in (1, 2):
            own = sql.Identifier(f"r{region}_data")
            conn.execute(sql.SQL("GRANT INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA {} TO matcher_owner").format(own))
            for remote in ("transport_c", "transport_r1", "transport_r2"):
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(own, sql.Identifier(remote)))
                conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(own, sql.Identifier(remote)))
                conn.execute(sql.SQL("GRANT INSERT ON {}.processed_commands TO {}").format(own, sql.Identifier(remote)))
        # Replicas have no DML grant to application, transport or maintenance role.
        if region == 0:
            conn.execute("GRANT INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA cat TO matcher_owner")


def subscriptions(config: dict) -> None:
    for target in range(3):
        with psycopg.connect(dsn(config, target), autocommit=True) as conn:
            sources = (1, 2) if target == 0 else (0, 3 - target)
            for source in sources:
                name = f"from_{'c' if source == 0 else 'r' + str(source)}"
                publication = "cat_catalog" if source == 0 else f"r{source}_working"
                if conn.execute("SELECT 1 FROM pg_subscription WHERE subname=%s", (name,)).fetchone():
                    continue
                slot = f"matcher_{source}_to_{target}"
                conn.execute(sql.SQL("CREATE SUBSCRIPTION {} CONNECTION {} PUBLICATION {} WITH (copy_data=true, create_slot=true, enabled=true, slot_name={})")
                             .format(sql.Identifier(name), sql.Literal(dsn(config, source, "matcher_repl")), sql.Identifier(publication), sql.Literal(slot)))
                print(f"Replication: {source} -> {target}")


def wait_ready(config: dict, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = True
        for region in range(3):
            with psycopg.connect(dsn(config, region)) as conn:
                pending = conn.execute("SELECT count(*) FROM pg_subscription_rel WHERE srsubstate <> 'r'").fetchone()[0]
                count = conn.execute("SELECT count(*) FROM pg_subscription").fetchone()[0]
                ready = ready and pending == 0 and count == 2
        if ready:
            print("All six subscriptions and their initial table copies are ready.")
            return
        time.sleep(0.5)
    raise TimeoutError("Initial replication did not reach ready state within timeout; inspect .local/postgres-*.log")


def setup(config: dict) -> None:
    for region in range(3):
        install_roles(config, region)
        apply_migrations(config, region)
    for region in range(3):
        configure_fdw(config, region)
    subscriptions(config)
    wait_ready(config)
    print("Setup complete. Reapplying unchanged migrations/topology is safe.")


def compose_environment(config: dict) -> None:
    compose = json.loads(json.dumps(config))
    compose["pg_bin"] = None
    for region, host in enumerate(("central", "region1", "region2")):
        compose["nodes"][region].update(host=host, port=5432, app_port=8200 + region)
    (RUNTIME / "compose-config.json").write_text(json.dumps(compose, indent=2), encoding="utf-8")
    lines = [f"POSTGRES_PASSWORD={config['passwords']['postgres']}"]
    for region in range(3):
        lines += [f"DATABASE_URL_{region}={dsn(compose, region, 'matcher_app')}",
                  f"WORKER_DATABASE_URL_{region}={dsn(compose, region, 'matcher_worker')}",
                  f"SESSION_SECRET_{region}={config['session_secrets'][region]}"]
    (RUNTIME / "compose.env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Generated .local/compose.env and compose-config.json; keep both private.")
