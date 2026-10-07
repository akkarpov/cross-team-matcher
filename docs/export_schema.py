"""Export installed logical relations and constraints into a Sphinx include."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg.rows import dict_row
from scripts.local import load_config, dsn


def export() -> Path:
    """Read the configured R1 schema without exporting any application rows."""
    config = load_config()
    with psycopg.connect(dsn(config, 1), row_factory=dict_row) as conn:
        tables = conn.execute("""SELECT n.nspname AS schema,c.relname AS name,c.oid,obj_description(c.oid) AS description
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE c.relkind IN ('r','p') AND n.nspname IN ('global','cat','private')
            ORDER BY n.nspname,c.relname""").fetchall()
        output = []
        for table in tables:
            title = table["schema"] + "." + table["name"]
            output.extend([title, "-"*len(title), "", table["description"] or "", "", ".. list-table:: Колонки", "   :header-rows: 1", "", "   * - Имя", "     - Тип", "     - NULL", "     - Описание"])
            columns = conn.execute("""SELECT a.attname,format_type(a.atttypid,a.atttypmod) AS type,a.attnotnull,
                col_description(a.attrelid,a.attnum) AS description FROM pg_attribute a
                WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (table["oid"],)).fetchall()
            for c in columns:
                output.extend([f"   * - ``{c['attname']}``", f"     - {c['type']}", f"     - {'нет' if c['attnotnull'] else 'да'}", f"     - {c['description'] or '—'}"])
            constraints = conn.execute("SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint WHERE conrelid=%s ORDER BY conname", (table["oid"],)).fetchall()
            if constraints:
                output.extend(["", "Ограничения::", ""] + ["   " + c["def"] for c in constraints])
            output.append("")
    destination = Path(__file__).parent / "generated" / "schema.rst"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(output), encoding="utf-8")
    print("Schema dictionary: " + str(destination.relative_to(Path(__file__).resolve().parents[1])))
    return destination


if __name__ == "__main__":
    export()
