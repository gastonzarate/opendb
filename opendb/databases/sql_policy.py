"""Conservative PostgreSQL AST policy; grants remain the authorization boundary."""

import json

from pglast import ast
from pglast import parse_sql
from pglast.parser import parse_sql_json

MAX_SQL_LENGTH = 100_000
QUALIFIED_NAME_PARTS = 2

READ = {"SelectStmt"}
WRITE = {
    "InsertStmt",
    "UpdateStmt",
    "DeleteStmt",
    "CreateStmt",
    "AlterTableStmt",
    "IndexStmt",
    "ViewStmt",
    "DropStmt",
    "RenameStmt",
    "TruncateStmt",
}
FUNCTIONS = {
    "sum",
    "avg",
    "min",
    "max",
    "count",
    "abs",
    "round",
    "ceil",
    "floor",
    "lower",
    "upper",
    "length",
    "char_length",
    "trim",
    "btrim",
    "ltrim",
    "rtrim",
    "replace",
    "substring",
    "concat",
    "concat_ws",
    "coalesce",
    "nullif",
    "greatest",
    "least",
    "now",
    "date_trunc",
    "date_part",
    "extract",
    "to_char",
    "to_date",
    "to_timestamp",
    "json_build_object",
    "jsonb_build_object",
    "jsonb_array_elements",
    "jsonb_to_recordset",
    "md5",
    "json_agg",
    "jsonb_agg",
    "array_agg",
    "string_agg",
    "row_number",
    "rank",
    "dense_rank",
    "lag",
    "lead",
    "first_value",
    "last_value",
    "bool_and",
    "bool_or",
    "generate_series",
    "unnest",
}
TYPES = {
    "int2",
    "int4",
    "int8",
    "smallint",
    "integer",
    "bigint",
    "serial",
    "bigserial",
    "serial4",
    "serial8",
    "numeric",
    "decimal",
    "float4",
    "float8",
    "real",
    "double",
    "text",
    "varchar",
    "bpchar",
    "char",
    "bool",
    "boolean",
    "date",
    "timestamp",
    "timestamptz",
    "time",
    "timetz",
    "interval",
    "uuid",
    "json",
    "jsonb",
    "bytea",
    "vector",
}
ALTER = {
    "AT_AddColumn",
    "AT_DropColumn",
    "AT_AlterColumnType",
    "AT_SetNotNull",
    "AT_DropNotNull",
    "AT_ColumnDefault",
    "AT_AddConstraint",
    "AT_DropConstraint",
}

# Only these relation-source fields resolve unqualified names as CTEs.
# In particular, INSERT/UPDATE/DELETE targets always name physical relations.
CTE_REFERENCE_FIELDS = {
    ast.SelectStmt: {"fromClause"},
    ast.UpdateStmt: {"fromClause"},
    ast.DeleteStmt: {"usingClause"},
    ast.JoinExpr: {"larg", "rarg"},
    ast.RangeTableSample: {"relation"},
}


def _strings(items):
    return [x["String"]["sval"] for x in items]


def _validate_relations(node, ctes=frozenset(), *, cte_reference=False):
    """Follow PostgreSQL's lexical WITH scopes using the typed parse tree.

    The JSON parser omits node tags on some fields, including write targets and
    UNION branches. Typed nodes retain those boundaries and every RangeVar.
    Immutable bindings prevent a child query from changing its parent's scope.
    """
    if isinstance(node, (tuple, list)):
        for child in node:
            _validate_relations(child, ctes, cte_reference=cte_reference)
        return
    if not isinstance(node, ast.Node):
        return
    if isinstance(node, ast.RangeVar):
        if node.catalogname or (
            node.schemaname != "data"
            and not (cte_reference and node.schemaname is None and node.relname in ctes)
        ):
            msg = "Use explicitly qualified data tables or views"
            raise ValueError(msg)
        return

    with_clause = getattr(node, "withClause", None)
    if with_clause is not None:
        if with_clause.recursive:
            # Recursive WITH makes the entire local group visible to each CTE.
            ctes = ctes | {cte.ctename for cte in with_clause.ctes}
        for cte in with_clause.ctes:
            # Ordinary WITH exposes only outer bindings and earlier siblings.
            # Add this name afterward: a same-named outer CTE remains visible
            # inside a nonrecursive definition that shadows it.
            _validate_relations(cte, ctes)
            ctes = ctes | {cte.ctename}

    reference_fields = CTE_REFERENCE_FIELDS.get(type(node), ())
    for field in node:
        if field != "withClause":
            _validate_relations(
                getattr(node, field), ctes, cte_reference=field in reference_fields
            )


def validate_sql(statement: str, *, readonly: bool = False) -> None:
    if not isinstance(statement, str) or len(statement) > MAX_SQL_LENGTH:
        msg = "SQL must be a string of at most 100000 characters"
        raise ValueError(msg)
    try:
        parsed = json.loads(parse_sql_json(statement))
        tree = parse_sql(statement)
    except Exception as exc:
        msg = "Invalid PostgreSQL statement"
        raise ValueError(msg) from exc
    stmts = parsed.get("stmts", [])
    if len(stmts) != 1:
        msg = "Exactly one statement is required"
        raise ValueError(msg)
    root = stmts[0]["stmt"]
    allowed = READ if readonly else READ | WRITE
    if next(iter(root)) not in allowed:
        msg = "Statement is not supported"
        raise ValueError(msg)
    _validate_relations(tree[0].stmt)

    def walk(node):
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            for kind, value in node.items():
                if kind[0].isupper() and kind.endswith("Stmt") and kind not in allowed:
                    msg = "Nested statement is not supported"
                    raise ValueError(msg)
                if kind == "FuncCall":
                    names = _strings(value["funcname"])
                    if len(names) != 1 or names[0].lower() not in FUNCTIONS:
                        name = ".".join(names)
                        msg = (
                            f"Function is not supported: {name!r}. "
                            "See ingestion_guide.query_policy.allowed_functions"
                        )
                        raise ValueError(msg)
                if kind == "TypeName":
                    names = _strings(value["names"])
                    if names[-1] not in TYPES or (
                        len(names) > 1
                        and names[:-1] not in [["pg_catalog"], ["public"]]
                    ):
                        msg = "Type is not supported"
                        raise ValueError(msg)
                if kind == "A_Expr" and len(value.get("name", [])) > 1:
                    msg = "Qualified operators are not supported"
                    raise ValueError(msg)
                if kind == "AlterTableCmd" and value.get("subtype") not in ALTER:
                    msg = "Schema alteration is not supported"
                    raise ValueError(msg)
                if kind == "SelectStmt" and (
                    value.get("intoClause") or value.get("lockingClause")
                ):
                    msg = "SELECT INTO and row locks are not supported"
                    raise ValueError(msg)
                if kind == "IndexStmt" and (
                    value.get("concurrent")
                    or value.get("tableSpace")
                    or value.get("accessMethod", "btree")
                    not in {"btree", "hash", "gin", "gist", "hnsw", "ivfflat"}
                ):
                    msg = "Index option is not supported"
                    raise ValueError(msg)
                if kind == "CreateStmt" and (
                    value.get("tablespacename")
                    or value.get("ofTypename")
                    or value.get("inhRelations")
                    or value.get("partbound")
                    or value.get("options")
                ):
                    msg = "Table option is not supported"
                    raise ValueError(msg)
                if kind == "DropStmt":
                    if value.get("removeType") not in {
                        "OBJECT_TABLE",
                        "OBJECT_VIEW",
                        "OBJECT_INDEX",
                    }:
                        msg = "Only data tables, views or indexes can be dropped"
                        raise ValueError(msg)
                    for obj in value["objects"]:
                        names = _strings(obj.get("List", {}).get("items", []))
                        if len(names) != QUALIFIED_NAME_PARTS or names[0] != "data":
                            msg = "Only data schema objects can be dropped"
                            raise ValueError(msg)
                if kind == "RenameStmt" and value.get("renameType") not in {
                    "OBJECT_COLUMN",
                    "OBJECT_TABLE",
                    "OBJECT_INDEX",
                    "OBJECT_VIEW",
                }:
                    msg = "Rename is not supported"
                    raise ValueError(msg)
                if kind == "Constraint" and value.get("indexspace"):
                    msg = "Tablespace is not supported"
                    raise ValueError(msg)
                walk(value)

    walk(root)
