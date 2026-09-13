"""Describe only caller-visible relations; never return SQL definitions."""

import hashlib
import json

import psycopg
from psycopg.rows import dict_row

from .install import CatalogError

RELATIONS = """
SELECT c.oid, c.relname, c.relkind, c.relrowsecurity
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='data' AND c.relkind IN ('r','p','v','m')
  AND pg_catalog.has_schema_privilege(n.oid, 'USAGE')
  AND pg_catalog.has_table_privilege(c.oid, 'SELECT')
ORDER BY c.relname
"""


def _annotations(cur):
    cur.execute(
        "SELECT pg_catalog.has_schema_privilege(n.oid, 'USAGE') AND "
        "pg_catalog.has_function_privilege(p.oid, 'EXECUTE') AS allowed "
        "FROM pg_catalog.pg_namespace n JOIN pg_catalog.pg_proc p "
        "ON p.pronamespace=n.oid WHERE n.nspname='opendb_catalog' "
        "AND p.proname='read_annotations' AND p.pronargs=0",
    )
    permission = cur.fetchone()
    if not permission or not permission["allowed"]:
        return {}
    cur.execute("SELECT opendb_catalog.read_annotations() AS annotations")
    return {
        (int(item["relation_oid"]), item["column_number"]): {
            "description": item["description"],
            "metadata": item["metadata"],
        }
        for item in cur.fetchone()["annotations"]
    }


def _columns(cur, oid):
    cur.execute(
        "SELECT a.attnum, a.attname AS name, "
        "pg_catalog.format_type(a.atttypid,a.atttypmod) AS type, "
        "NOT a.attnotnull AS nullable, a.attidentity AS identity, "
        "a.attgenerated AS generated, "
        "a.atthasdef AS has_default "
        "FROM pg_catalog.pg_attribute a WHERE a.attrelid=%s AND a.attnum>0 "
        "AND NOT a.attisdropped ORDER BY a.attnum",
        (oid,),
    )
    return cur.fetchall()


def _constraints(cur, oid, visible, column_names):
    cur.execute(
        "SELECT conname, contype, conkey, confrelid, confkey, "
        "confupdtype, confdeltype, "
        "condeferrable, condeferred FROM pg_catalog.pg_constraint WHERE conrelid=%s "
        "AND contype IN ('p','u','f') ORDER BY conname",
        (oid,),
    )
    primary, unique, foreign = [], [], []
    for row in cur.fetchall():
        columns = [column_names[oid][number] for number in row["conkey"]]
        if row["contype"] == "p":
            primary = columns
        elif row["contype"] == "u":
            unique.append({"name": row["conname"], "columns": columns})
        elif row["confrelid"] in visible:
            foreign.append(
                {
                    "name": row["conname"],
                    "columns": columns,
                    "references": {
                        "schema": "data",
                        "table": visible[row["confrelid"]],
                        "columns": [
                            column_names[row["confrelid"]][number]
                            for number in row["confkey"]
                        ],
                    },
                    "on_update": row["confupdtype"],
                    "on_delete": row["confdeltype"],
                    "deferrable": row["condeferrable"],
                    "initially_deferred": row["condeferred"],
                }
            )
    return {
        "primary_key": primary,
        "unique_constraints": unique,
        "foreign_keys": foreign,
    }


def _owner_revision(cur):
    # Definitions are only hashed for the actual data-schema owner, never exposed.
    # This catches default/check/index/view changes without disclosing expressions.
    cur.execute(
        "SELECT nspowner=(SELECT oid FROM pg_catalog.pg_roles "
        "WHERE rolname=current_user) AS owner "
        "FROM pg_catalog.pg_namespace WHERE nspname='data'",
    )
    row = cur.fetchone()
    if not row or not row["owner"]:
        return []
    cur.execute("""
        SELECT 'relation' AS kind, c.oid::text AS object_id,
               c.relname || ':' || c.xmin::text AS revision
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='data'
        UNION ALL
        SELECT 'default', d.oid::text, pg_catalog.pg_get_expr(d.adbin,d.adrelid)
        FROM pg_catalog.pg_attrdef d JOIN pg_catalog.pg_class c ON c.oid=d.adrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='data'
        UNION ALL
        SELECT 'constraint', k.oid::text, pg_catalog.pg_get_constraintdef(k.oid)
        FROM pg_catalog.pg_constraint k
        JOIN pg_catalog.pg_namespace n ON n.oid=k.connamespace
        WHERE n.nspname='data'
        UNION ALL
        SELECT 'view', c.oid::text, pg_catalog.pg_get_viewdef(c.oid)
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='data' AND c.relkind IN ('v','m')
        ORDER BY kind, object_id
    """)
    return cur.fetchall()


def describe(conn):
    try:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT pg_catalog.current_setting('search_path') AS path")
            original_path = cur.fetchone()["path"]
            cur.execute(
                "SELECT pg_catalog.set_config('search_path', 'pg_catalog', true)"
            )
            cur.execute(RELATIONS)
            relations = cur.fetchall()
            visible = {row["oid"]: row["relname"] for row in relations}
            columns = {oid: _columns(cur, oid) for oid in visible}
            column_names = {
                oid: {col["attnum"]: col["name"] for col in cols}
                for oid, cols in columns.items()
            }
            annotations = _annotations(cur)
            objects, structural = [], []
            for relation in relations:
                oid = relation["oid"]
                item = {
                    "schema": "data",
                    "name": relation["relname"],
                    "kind": {
                        "r": "table",
                        "p": "partitioned_table",
                        "v": "view",
                        "m": "materialized_view",
                    }[relation["relkind"]],
                    "row_security": relation["relrowsecurity"],
                    "columns": [
                        {key: value for key, value in column.items() if key != "attnum"}
                        for column in columns[oid]
                    ],
                    **_constraints(cur, oid, visible, column_names),
                }
                # Copy before annotations so semantic edits never masquerade as DDL.
                structural.append(json.loads(json.dumps(item)))
                item.update(
                    annotations.get((oid, 0), {"description": "", "metadata": {}})
                )
                for column, raw in zip(item["columns"], columns[oid], strict=True):
                    column.update(
                        annotations.get(
                            (oid, raw["attnum"]), {"description": "", "metadata": {}}
                        )
                    )
                objects.append(item)
            fingerprint = hashlib.sha256(
                json.dumps(
                    {"objects": structural, "revision": _owner_revision(cur)},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            cur.execute(
                "SELECT pg_catalog.set_config('search_path', %s, true)",
                (original_path,),
            )
            return {"schema": "data", "fingerprint": fingerprint, "objects": objects}
    except psycopg.Error:
        msg = "catalog_unavailable"
        raise CatalogError(msg, "Catalog could not be read; retry discovery.") from None
