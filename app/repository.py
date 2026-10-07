"""Role-scoped reads of logical, search and analytics relations.

Authoritative cards read owner-qualified ``global`` rows; discovery reads
local asynchronous copies. Both use the same local connection factory.
"""

from datetime import date, datetime, timezone
from uuid import UUID
from psycopg import sql
from app.db import ActorDatabase
from app.errors import DomainError


def require_role(user: dict, *roles: str) -> None:
    """Reject a read before querying if no permitted role is present."""
    if not set(user["roles"]).intersection(roles):
        raise DomainError("forbidden", 403)


def classify_freshness(info: dict) -> dict:
    """Flag unconfirmed freshness using the receiver clock and message time.

    Sixty seconds without a message is an observation threshold, not measured
    transaction lag. A ready initial copy does not prove receiver connectivity.
    """
    now = datetime.fromisoformat(info["checked_at"]) if info.get("checked_at") else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    sources = []
    for original in info.get("sources", []):
        source = dict(original)
        age = None
        if source.get("last_received_at"):
            try:
                timestamp = datetime.fromisoformat(source["last_received_at"])
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                age = max(0, (now - timestamp).total_seconds())
            except (ValueError, TypeError):
                pass
        source["seconds_since_message"] = age
        source["is_stale"] = source.get("enabled") is not True or source.get("ready") is not True or age is None or age > 60
        sources.append(source)
    return {**info, "sources": sources, "is_stale": not sources or any(s["is_stale"] for s in sources), "observation_threshold_seconds": 60}


class Repository:
    """Read adapter bound to a signed-in account and its instance region.

    :param db: Database with transaction-local actor identity.
    :param user: Active local account and role codes.
    :param region: Logical owner of this application instance.
    """

    def __init__(self, db: ActorDatabase, user: dict, region: int):
        self.db, self.user, self.region = db, user, region

    def many(self, query: str, params: tuple = ()) -> list[dict]:
        """Execute one parameterised logical query in a short transaction."""
        with self.db.connect() as conn:
            return conn.execute(query, params).fetchall()

    def one(self, query: str, params: tuple = ()) -> dict:
        """Fetch a required row or raise the public not-found error."""
        rows = self.many(query, params)
        if not rows:
            raise DomainError("not_found", 404)
        return rows[0]

    def freshness(self) -> dict:
        """Return observed subscription health from the local database."""
        return classify_freshness(self.one("SELECT search.freshness() AS data")["data"])

    def catalog(self) -> dict:
        """Return the locally available competency and region catalogs."""
        return {"items": self.many("SELECT * FROM cat.competencies ORDER BY active DESC, name, competency_id"),
                "regions": self.many("SELECT * FROM cat.regions ORDER BY region_id")}

    def employees(self, query: str = "") -> dict:
        """List permitted working profiles, enriching them with verified skills."""
        clauses, values = ["e.nickname ILIKE %s"], [f"%{query[:100]}%"]
        roles = set(self.user["roles"])
        if not roles.intersection({"MANAGER", "ANALYST"}):
            clauses.append("e.owner_region = %s")
            values.append(self.region)
            if "EDITOR" not in roles:
                require_role(self.user, "EMPLOYEE")
                clauses.append("e.employee_id = %s")
                values.append(self.user["employee_id"])
        rows = self.many("""SELECT e.*, COALESCE((SELECT jsonb_agg(jsonb_build_object(
            'competency_id',c.competency_id,'name',c.name,'kind',c.kind) ORDER BY c.name)
            FROM search.employee_competencies ec JOIN cat.competencies c USING(competency_id)
            WHERE ec.owner_region=e.owner_region AND ec.employee_id=e.employee_id),'[]'::jsonb) AS competencies
            FROM search.employee_profiles e WHERE """ + " AND ".join(clauses) +
            " ORDER BY (e.owner_region=%s) DESC,e.nickname,e.employee_id LIMIT 500", tuple(values + [self.region]))
        return {"items": rows, "freshness": self.freshness()}

    def employee(self, owner: int, identifier: UUID) -> dict:
        """Load profile, calendar, commitments and contextual reviews."""
        roles = set(self.user["roles"])
        is_self = owner == self.region and str(identifier) == str(self.user.get("employee_id"))
        if not ("MANAGER" in roles or "ANALYST" in roles or ("EDITOR" in roles and owner == self.region) or is_self):
            raise DomainError("forbidden", 403)
        source = "global" if owner == self.region else "search"
        profile_table = "employees" if source == "global" else "employee_profiles"
        result = {"employee": self.one(f"SELECT * FROM {source}.{profile_table} WHERE owner_region=%s AND employee_id=%s", (owner, identifier))}
        for key, table in (("capacity", "capacity_periods"), ("unavailability", "unavailability_periods"), ("assignments", "assignments")):
            result[key] = self.many(f"SELECT * FROM {source}.{table} WHERE owner_region=%s AND employee_id=%s ORDER BY date_from", (owner, identifier))
        result["skills"] = self.many(f"SELECT ec.*,c.name,c.kind FROM {source}.employee_competencies ec JOIN cat.competencies c USING(competency_id) WHERE ec.owner_region=%s AND ec.employee_id=%s ORDER BY c.name", (owner, identifier))
        result["reviews"] = self.many("""SELECT r.*,p.name AS project_name FROM search.reviews r
            LEFT JOIN search.projects p ON p.owner_region=r.owner_region AND p.project_id=r.project_id
            WHERE r.employee_region=%s AND r.employee_id=%s ORDER BY r.created_at DESC""", (owner, identifier))
        if owner == self.region and (is_self or "EDITOR" in roles):
            private = self.many("SELECT full_name,contact_email,contact_phone FROM private.employee_private WHERE owner_region=%s AND employee_id=%s", (owner, identifier))
            result["employee"].update(private[0] if private else {})
            result["private"] = private[0] if private else {}
        result["freshness"] = self.freshness()
        return result

    def projects(self) -> dict:
        """Managers read their authoritative regional projects."""
        require_role(self.user, "MANAGER", "ANALYST")
        if self.region == 0:
            return {"items": self.many("SELECT * FROM search.projects ORDER BY updated_at DESC")}
        return {"items": self.many("""SELECT p.*,
            (SELECT count(*) FROM global.project_positions pp WHERE pp.owner_region=p.owner_region AND pp.project_id=p.project_id) AS position_count,
            (SELECT count(*) FROM global.invitations i JOIN global.project_positions pp ON pp.owner_region=i.owner_region AND pp.position_id=i.position_id
             WHERE pp.owner_region=p.owner_region AND pp.project_id=p.project_id AND i.status='ACCEPTED') AS filled_positions
            FROM global.projects p WHERE p.owner_region=%s AND p.manager_id=%s ORDER BY p.updated_at DESC""", (self.region, self.user["account_id"]))}

    def project(self, owner: int, identifier: UUID) -> dict:
        """Return a project and its positions with immutable offer history."""
        require_role(self.user, "MANAGER", "ANALYST")
        source = "global" if owner == self.region else "search"
        project = self.one(f"SELECT * FROM {source}.projects WHERE owner_region=%s AND project_id=%s", (owner, identifier))
        if "ANALYST" not in self.user["roles"] and str(project["manager_id"]) != str(self.user["account_id"]):
            raise DomainError("forbidden", 403)
        positions = self.many(f"""SELECT pp.*,COALESCE((SELECT jsonb_agg(jsonb_build_object('competency_id',c.competency_id,'name',c.name))
            FROM {source}.position_competencies pc JOIN cat.competencies c USING(competency_id)
            WHERE pc.owner_region=pp.owner_region AND pc.position_id=pp.position_id),'[]'::jsonb) AS competencies
            FROM {source}.project_positions pp WHERE pp.owner_region=%s AND pp.project_id=%s ORDER BY pp.title""", (owner, identifier))
        invitations = self.many(f"""SELECT i.*,pp.title AS position_title,e.nickname FROM {source}.invitations i
            JOIN {source}.project_positions pp ON pp.owner_region=i.owner_region AND pp.position_id=i.position_id
            LEFT JOIN search.employee_profiles e ON e.owner_region=i.employee_region AND e.employee_id=i.employee_id
            WHERE pp.owner_region=%s AND pp.project_id=%s ORDER BY i.created_at DESC""", (owner, identifier))
        assignments = self.many("SELECT * FROM search.assignments WHERE project_region=%s AND project_id=%s ORDER BY date_from", (owner, identifier))
        reviews = self.many(f"SELECT * FROM {source}.reviews WHERE owner_region=%s AND project_id=%s ORDER BY created_at DESC", (owner, identifier))
        return {"project": project, "positions": positions, "invitations": invitations, "assignments": assignments, "reviews": reviews, "freshness": self.freshness()}

    def invitations(self) -> dict:
        """Read incoming offers exclusively for the current employee."""
        require_role(self.user, "EMPLOYEE")
        return {"items": self.many("""SELECT ir.*,ir.decision AS status,p.name AS project_name,
            pp.title AS position_title FROM global.invitation_responses ir
            LEFT JOIN search.projects p ON p.owner_region=ir.project_region AND p.project_id=ir.project_id
            LEFT JOIN search.project_positions pp ON pp.owner_region=ir.project_region AND pp.position_id=ir.position_id
            WHERE ir.owner_region=%s AND ir.employee_id=%s ORDER BY ir.updated_at DESC""", (self.region, self.user["employee_id"]))}

    def assignments(self) -> dict:
        """Read own assignments, or assignments to the manager's projects."""
        require_role(self.user, "EMPLOYEE", "MANAGER", "ANALYST")
        if "MANAGER" in self.user["roles"] or "ANALYST" in self.user["roles"]:
            condition = "true" if "ANALYST" in self.user["roles"] else "p.owner_region=%s AND p.manager_id=%s"
            values = () if condition == "true" else (self.region, self.user["account_id"])
            table = "search.assignments"
        else:
            condition, values, table = "a.owner_region=%s AND a.employee_id=%s", (self.region, self.user["employee_id"]), "global.assignments"
        return {"items": self.many(f"""SELECT a.*,p.name AS project_name,pp.title AS position_title,e.nickname
            FROM {table} a LEFT JOIN search.projects p ON p.owner_region=a.project_region AND p.project_id=a.project_id
            LEFT JOIN search.project_positions pp ON pp.owner_region=a.project_region AND pp.position_id=a.position_id
            LEFT JOIN search.employee_profiles e ON e.owner_region=a.owner_region AND e.employee_id=a.employee_id
            WHERE {condition} ORDER BY a.date_from DESC""", values)}

    def reviews(self) -> dict:
        """Return reviews with project context, respecting employee visibility."""
        require_role(self.user, "EMPLOYEE", "MANAGER", "ANALYST")
        condition, params = "true", ()
        if not set(self.user["roles"]).intersection({"MANAGER", "ANALYST"}):
            condition, params = "r.employee_region=%s AND r.employee_id=%s", (self.region, self.user["employee_id"])
        return {"items": self.many(f"""SELECT r.*,p.name AS project_name,e.nickname FROM search.reviews r
            LEFT JOIN search.projects p ON p.owner_region=r.owner_region AND p.project_id=r.project_id
            LEFT JOIN search.employee_profiles e ON e.owner_region=r.employee_region AND e.employee_id=r.employee_id
            WHERE {condition} ORDER BY r.created_at DESC""", params)}

    def events(self) -> dict:
        """Expose a bounded business audit trail, never credential records."""
        require_role(self.user, "MANAGER", "EDITOR", "ANALYST")
        condition, values = ("true", ()) if "ANALYST" in self.user["roles"] else ("owner_region=%s AND actor_id=%s", (self.region, self.user["account_id"]))
        return {"items": self.many(f"SELECT * FROM search.workflow_events WHERE {condition} ORDER BY occurred_at DESC LIMIT 200", values)}

    def dashboard(self) -> dict:
        """Assemble a role-specific summary without contacting remote owners."""
        roles = set(self.user["roles"])
        result = {"node_region": self.region, "freshness": self.freshness(), "generated_at": datetime.now(timezone.utc).isoformat()}
        if roles.intersection({"MANAGER", "EDITOR", "ANALYST"}):
            result["stats"] = self.one("""SELECT
                (SELECT count(*) FROM search.employee_profiles WHERE active) AS employees,
                (SELECT count(*) FROM search.projects WHERE status NOT IN ('CANCELLED','COMPLETED')) AS projects,
                (SELECT count(*) FROM search.assignments WHERE status IN ('ACTIVE','CONFLICT')) AS assignments,
                (SELECT count(*) FROM cat.competencies WHERE active) AS competencies,
                (SELECT count(*) FROM search.invitations WHERE status IN ('PENDING','SENT','DELIVERED')) AS invitations,
                (SELECT count(*) FROM search.assignments WHERE status='CONFLICT') AS conflicts""")
            result["events"] = self.events()["items"][:8]
            result["projects"] = [p for p in self.projects()["items"] if p["status"] not in ("COMPLETED", "CANCELLED")][:6] if roles.intersection({"MANAGER", "ANALYST"}) else []
        elif "EMPLOYEE" in roles:
            invitations, assignments = self.invitations()["items"], self.assignments()["items"]
            result.update(invitations=invitations, assignments=assignments, events=[], projects=[])
            result["stats"] = {"invitations": sum(i["decision"] == "PENDING" for i in invitations), "assignments": sum(a["status"] == "ACTIVE" for a in assignments), "reviews": len(self.reviews()["items"])}
        else:
            result.update(stats={"competencies": len(self.catalog()["items"])}, events=[], projects=[])
        result["counts"] = result["stats"]
        result["regions"] = self.many("""SELECT e.owner_region,count(*) AS employees,
            (SELECT count(*) FROM search.assignments a WHERE a.owner_region=e.owner_region AND a.status IN ('ACTIVE','CONFLICT')) AS assignments
            FROM search.employee_profiles e WHERE e.active GROUP BY e.owner_region ORDER BY e.owner_region""") if roles.intersection({"MANAGER", "EDITOR", "ANALYST"}) else []
        return result

    def analytics(self) -> dict:
        """Read consolidated reports without live queries to regional owners."""
        require_role(self.user, "ANALYST")
        if self.region != 0:
            raise DomainError("forbidden", 403)
        with self.db.connect() as conn:
            views = conn.execute("SELECT table_name FROM information_schema.views WHERE table_schema='analytics' ORDER BY table_name").fetchall()
            result = {v["table_name"]: conn.execute(sql.SQL("SELECT * FROM analytics.{} LIMIT 1000").format(sql.Identifier(v["table_name"]))).fetchall() for v in views}
        result.update(freshness=self.freshness(), generated_at=datetime.now(timezone.utc).isoformat())
        return result

    def search(self, filters: dict) -> dict:
        """Apply all required skills and daily capacity before pagination."""
        require_role(self.user, "MANAGER")
        mode = {"LOCAL_FIRST": "OWN_FIRST", "LOCAL": "OWN_ONLY", "OWN": "OWN_ONLY"}.get(filters["region_mode"], filters["region_mode"])
        if mode not in (None, "OWN_FIRST", "OWN_ONLY", "ALL"):
            raise DomainError("payload_invalid", 422)
        if filters.get("position_id"):
            result = self.one("SELECT search.candidates(%s::smallint,%s::uuid,%s,%s) AS data", (filters.get("position_owner") or self.region, filters["position_id"], mode, filters["page"]))["data"]
            result["position"] = self.one("""SELECT pp.*,p.date_from,p.date_to,
                ARRAY(SELECT competency_id FROM search.position_competencies pc WHERE pc.owner_region=pp.owner_region AND pc.position_id=pp.position_id) AS competency_ids
                FROM search.project_positions pp JOIN search.projects p ON p.owner_region=pp.owner_region AND p.project_id=pp.project_id
                WHERE pp.owner_region=%s AND pp.position_id=%s""", (filters.get("position_owner") or self.region, filters["position_id"]))
        else:
            mode = mode or "OWN_FIRST"
            result = self.one("SELECT search.find_candidates(%s::date,%s::date,%s::numeric,%s::uuid[],%s::int,%s,%s::int,%s) AS data", (
                filters["date_from"], filters["date_to"], filters["hours_per_week"], filters["competencies"], filters["experience_months"], mode, filters["page"], filters["query"]))["data"]
        result["freshness"] = self.freshness()
        return result
