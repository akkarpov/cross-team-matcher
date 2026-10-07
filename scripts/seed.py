"""Deterministic, fictional 300-person dataset; secrets are generated locally."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid

import psycopg
from psycopg import sql

from scripts.local import RUNTIME, dsn

NAMESPACE = uuid.UUID("1c3a1d66-4dd9-4d49-883c-9151c14d1717")


def identity(label: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, label)


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)
    return f"pbkdf2_sha256$600000${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def seed(config: dict) -> None:
    names = ["Python", "JavaScript", "TypeScript", "SQL", "PostgreSQL", "React", "FastAPI",
             "Docker", "Kubernetes", "Git", "Figma", "UX Research", "UI Design", "Product Discovery",
             "Data Analysis", "Machine Learning", "Java", "C++", "Go", "Rust", "C#", "Redis",
             "Kafka", "Linux", "Terraform", "CI/CD", "Testing", "Playwright", "Pytest",
             "System Design", "Business Analysis", "Scrum", "Project Management", "Communication",
             "Technical Writing", "Accessibility", "Security", "GraphQL", "REST API", "MongoDB",
             "Elasticsearch", "Prometheus", "Grafana", "Airflow", "dbt", "Power BI", "Tableau",
             "Financial Modeling", "Statistics", "Presentation"]
    competencies = names + [f"{name} · Advanced" for name in names] + [f"{name} · Architecture" for name in names]
    with psycopg.connect(dsn(config, 0)) as conn:
        conn.execute("INSERT INTO cat.regions(region_id,code,name) VALUES (0,'C','Центральный офис'),(1,'R1','Европа'),(2,'R2','Азия') ON CONFLICT DO NOTHING")
        for index, name in enumerate(competencies):
            conn.execute("INSERT INTO cat.competencies(competency_id,name,kind,description) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (identity(f"competency:{index}"), name, "TOOL" if index % 4 == 0 else "SKILL", "Синтетический каталог учебного стенда"))
    deadline = time.monotonic() + 30
    for region in (1, 2):
        while True:
            with psycopg.connect(dsn(config, region)) as conn:
                count = conn.execute("SELECT count(*) FROM cat.competencies").fetchone()[0]
            if count >= 150:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Catalog replication not ready for seed")
            time.sleep(0.2)
    credentials_path = RUNTIME / "demo-credentials.json"
    credentials = json.loads(credentials_path.read_text(encoding="utf-8")) if credentials_path.exists() else {"synthetic": True, "accounts": []}
    existing = {item["login"]: item for item in credentials["accounts"]}
    first_names = ["alex", "mira", "leo", "anna", "max", "sofia", "nick", "eva", "sam", "kate", "dan", "julia", "art", "vera", "tim"]
    for region in range(3):
        with psycopg.connect(dsn(config, region)) as conn:
            if region:
                schema = sql.Identifier(f"r{region}_data")
                for index in range(150):
                    employee = identity(f"employee:{region}:{index}")
                    conn.execute(sql.SQL("INSERT INTO {}.employees(owner_region,employee_id,nickname,experience_months) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING").format(schema),
                                 (region, employee, f"{first_names[index % 15]}.{region}{index + 1:03d}", 12 + (index * 7) % 145))
                    conn.execute("INSERT INTO private.employee_private(owner_region,employee_id,full_name,contact_email) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                 (region, employee, f"Учебный сотрудник {region}-{index + 1:03d}", f"person{region}-{index + 1}@example.invalid"))
                    skill_indices = set(range(12)) if index < 5 else {(index * 7 + offset * 11) % 150 for offset in range(12)}
                    for skill in skill_indices:
                        conn.execute(sql.SQL("INSERT INTO {}.employee_competencies(owner_region,employee_id,competency_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING").format(schema),
                                     (region, employee, identity(f"competency:{skill}")))
                    conn.execute(sql.SQL("INSERT INTO {}.capacity_periods(owner_region,id,employee_id,date_from,date_to,hours_per_week) VALUES (%s,%s,%s,'2026-01-01','2027-12-31',40) ON CONFLICT DO NOTHING").format(schema),
                                 (region, identity(f"capacity:{region}:{index}"), employee))
            accounts = [("admin.c", "CATALOG_ADMIN", None), ("analyst.c", "ANALYST", None)] if region == 0 else [
                (f"manager.r{region}", "MANAGER", None), (f"editor.r{region}", "EDITOR", None),
                (f"employee.r{region}", "EMPLOYEE", identity(f"employee:{region}:0"))]
            for login, role, employee in accounts:
                item = existing.get(login)
                if item is None:
                    item = {"login": login, "password": secrets.token_urlsafe(14), "region": region,
                            "role": role, "url": f"http://127.0.0.1:{config['nodes'][region]['app_port']}"}
                    credentials["accounts"].append(item)
                account_id = identity(f"account:{login}")
                conn.execute("INSERT INTO private.accounts(account_id,owner_region,employee_id,login,password_hash) VALUES (%s,%s,%s,%s,%s) ON CONFLICT(account_id) DO UPDATE SET password_hash=EXCLUDED.password_hash",
                             (account_id, region, employee, login, password_hash(item["password"])))
                conn.execute("INSERT INTO private.account_roles(account_id,role_code,scope_id) VALUES (%s,%s,'*') ON CONFLICT DO NOTHING", (account_id, role))
            if region:
                project_names = ["Atlas · единый рабочий кабинет", "Pulse · аналитика команды", "Orbit · мобильный опыт", "Horizon · инфраструктура данных"]
                for index, name in enumerate(project_names):
                    project = identity(f"project:{region}:{index}")
                    conn.execute(sql.SQL("INSERT INTO {}.projects(owner_region,project_id,manager_id,name,description,date_from,date_to) VALUES (%s,%s,%s,%s,%s,'2026-11-02','2027-02-26') ON CONFLICT DO NOTHING").format(schema),
                                 (region, project, identity(f"account:manager.r{region}"), name,
                                  "Демонстрационный проект: команда из специалистов разных регионов, прозрачные условия участия и подтверждение сотрудником."))
                    for position_index, title in enumerate(("Backend Engineer", "Product Designer", "Data Analyst")):
                        position = identity(f"position:{region}:{index}:{position_index}")
                        conn.execute(sql.SQL("INSERT INTO {}.project_positions(owner_region,position_id,project_id,title,min_experience,hours_per_week,search_mode) VALUES (%s,%s,%s,%s,12,%s,'OWN_FIRST') ON CONFLICT DO NOTHING").format(schema),
                                     (region, position, project, title, 10 + index * 2))
                        skill = [0, 10, 3][position_index]
                        conn.execute(sql.SQL("INSERT INTO {}.position_competencies(owner_region,position_id,competency_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING").format(schema),
                                     (region, position, identity(f"competency:{skill}")))
    credentials_path.write_text(json.dumps(credentials, indent=2, ensure_ascii=False), encoding="utf-8")
    print("Seed complete: 300 fictional employees, 150 competencies, 8 projects, 24 positions.")
    print("Generated demo credentials: .local/demo-credentials.json (never commit this file).")
