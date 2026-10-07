"""Print redacted PostgreSQL topology evidence for the laboratory defence."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg.rows import dict_row
from scripts.local import load_config, dsn


def inspect() -> dict:
    config = load_config()
    result = {}
    for region in range(3):
        with psycopg.connect(dsn(config, region), row_factory=dict_row) as conn:
            result[region] = {
                "version": conn.execute("SELECT version() AS version").fetchone()["version"],
                "partitions": conn.execute("SELECT p.relname AS logical_table,n.nspname AS physical_schema,c.relname AS physical_table,pg_get_expr(c.relpartbound,c.oid) AS bounds,c.relkind FROM pg_inherits i JOIN pg_class p ON p.oid=i.inhparent JOIN pg_namespace pn ON pn.oid=p.relnamespace JOIN pg_class c ON c.oid=i.inhrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE pn.nspname='global' ORDER BY 1,2,3").fetchall(),
                "foreign_servers": conn.execute("SELECT srvname,srvoptions FROM pg_foreign_server ORDER BY srvname").fetchall(),
                # Do not include umoptions: it contains service passwords.
                "mapping_roles": conn.execute("SELECT srvname,usename FROM pg_user_mappings ORDER BY 1,2").fetchall(),
                "publications": conn.execute("SELECT pubname,puballtables,pubinsert,pubupdate,pubdelete,pubtruncate,pubviaroot FROM pg_publication").fetchall(),
                "subscription_tables": conn.execute("SELECT s.subname,n.nspname,c.relname,r.srsubstate FROM pg_subscription_rel r JOIN pg_subscription s ON s.oid=r.srsubid JOIN pg_class c ON c.oid=r.srrelid JOIN pg_namespace n ON n.oid=c.relnamespace ORDER BY 1,2,3").fetchall(),
                "query_plan": [row["QUERY PLAN"] for row in conn.execute("EXPLAIN (VERBOSE,COSTS OFF) SELECT employee_id,nickname FROM global.employees WHERE owner_region=2")],
            }
    return result


if __name__ == "__main__":
    print(json.dumps(inspect(), indent=2, default=str))
