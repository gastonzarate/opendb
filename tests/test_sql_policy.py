import pytest


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT * FROM pg_catalog.pg_authid",
        "SELECT pg_read_file('/etc/passwd')",
        "SET ROLE postgres",
        "CREATE FUNCTION data.evil() RETURNS int LANGUAGE sql AS $$SELECT 1$$",
        "SELECT 1; DROP TABLE data.costs",
        "COPY data.costs TO PROGRAM 'id'",
        "SELECT * FROM public.secret",
        "SELECT set_config('role', 'postgres', false)",
        "CREATE TABLE data.costs (x int DEFAULT pg_sleep(1))",
        "SELECT data.evil()",
        "WITH wiped AS (DELETE FROM data.costs RETURNING *) SELECT * FROM wiped",
    ],
)
def test_reject_unsafe_sql(statement):
    from opendb.databases.sql_policy import validate_sql

    with pytest.raises(ValueError, match=r"supported|qualified|Exactly one"):
        validate_sql(statement, readonly=True)


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT sum(amount) FROM data.costs",
        "WITH totals AS (SELECT amount FROM data.costs) SELECT sum(amount) FROM totals",
        "SELECT category, count(*) FROM data.costs GROUP BY category",
    ],
)
def test_accept_select(statement):
    from opendb.databases.sql_policy import validate_sql

    validate_sql(statement, readonly=True)


@pytest.mark.parametrize("readonly", [True, False])
@pytest.mark.parametrize(
    "statement",
    [
        # An inner WITH does not authorize a physical relation in its parent.
        (
            "WITH unused AS (WITH pg_views AS (SELECT 1) SELECT 1) "
            "SELECT definition FROM pg_views"
        ),
        (
            "SELECT * FROM (WITH pg_views AS (SELECT 1) SELECT * FROM pg_views) q, "
            "pg_views"
        ),
        "SELECT (WITH pg_views AS (SELECT 1) SELECT 1), definition FROM pg_views",
        # Nor may an inner binding escape into a sibling CTE or UNION branch.
        (
            "WITH unused AS (WITH pg_views AS (SELECT 1) SELECT 1), "
            "exposed AS (SELECT definition FROM pg_views) SELECT * FROM exposed"
        ),
        (
            "(WITH pg_views AS (SELECT 'safe' AS definition) "
            "SELECT definition FROM pg_views) UNION ALL SELECT definition FROM pg_views"
        ),
        # Nonrecursive definitions cannot see themselves or later siblings.
        "WITH pg_views AS (SELECT definition FROM pg_views) SELECT * FROM pg_views",
        (
            "WITH exposed AS (SELECT definition FROM pg_views), "
            "pg_views AS (SELECT 1) SELECT * FROM exposed"
        ),
        (
            "WITH unused AS (WITH RECURSIVE pg_views(n) AS "
            "(SELECT 1 UNION ALL SELECT n+1 FROM pg_views WHERE n<2) SELECT 1) "
            "SELECT definition FROM pg_views"
        ),
        # Qualified relations never resolve to a CTE; quoted names retain case.
        "WITH pg_views AS (SELECT 1) SELECT * FROM pg_catalog.pg_views",
        "WITH pg_views AS (SELECT 1) SELECT * FROM public.pg_views",
        'WITH "PG_VIEWS" AS (SELECT 1) SELECT definition FROM pg_views',
    ],
)
def test_cte_bindings_do_not_authorize_out_of_scope_relations(statement, readonly):
    from opendb.databases.sql_policy import validate_sql

    with pytest.raises(ValueError, match="explicitly qualified"):
        validate_sql(statement, readonly=readonly)


@pytest.mark.parametrize("readonly", [True, False])
@pytest.mark.parametrize(
    "statement",
    [
        "WITH a AS (SELECT 1 AS n), b AS (SELECT n FROM a) SELECT * FROM b",
        (
            "WITH a AS MATERIALIZED (SELECT 1 AS n), "
            "b AS NOT MATERIALIZED (SELECT n FROM a) SELECT * FROM b"
        ),
        "WITH a AS (SELECT 1 AS n) SELECT * FROM (SELECT * FROM a) q",
        (
            "WITH a AS (SELECT 1 AS n) SELECT (SELECT n FROM a) "
            "WHERE EXISTS (SELECT 1 FROM a)"
        ),
        # A nonrecursive inner definition may still reference the outer binding.
        (
            "WITH a AS (SELECT 1 AS n) SELECT * FROM "
            "(WITH a AS (SELECT n+1 AS n FROM a) SELECT n FROM a) q"
        ),
        "WITH a AS (WITH b AS (SELECT 1 AS n) SELECT n FROM b) SELECT n FROM a",
        (
            "WITH pg_views AS (SELECT 'safe' AS definition) "
            "SELECT definition FROM pg_views"
        ),
        'WITH "PG_VIEWS" AS (SELECT 1) SELECT * FROM "PG_VIEWS"',
        (
            "WITH RECURSIVE a(n) AS (SELECT 1 UNION ALL "
            "SELECT n+1 FROM a WHERE n<3) SELECT * FROM a"
        ),
        "WITH RECURSIVE a AS (SELECT * FROM b), b AS (SELECT 1 AS n) SELECT * FROM a",
        "WITH a AS (SELECT 1 AS n) SELECT n FROM a UNION ALL SELECT n FROM a",
        (
            "(WITH a AS (SELECT 1 AS n) SELECT n FROM a) "
            "UNION ALL (WITH a AS (SELECT 2 AS n) SELECT n FROM a)"
        ),
        "WITH a AS (SELECT 1 AS n) SELECT a.n FROM a JOIN data.costs c ON true",
    ],
)
def test_lexically_visible_ctes_remain_supported(statement, readonly):
    from opendb.databases.sql_policy import validate_sql

    validate_sql(statement, readonly=readonly)


@pytest.mark.parametrize(
    "statement",
    [
        "WITH a AS (SELECT 1 AS id) INSERT INTO data.costs (id) SELECT id FROM a",
        (
            "WITH a AS (SELECT 1 AS id) UPDATE data.costs SET amount=1 "
            "FROM a WHERE costs.id=a.id"
        ),
        "WITH a AS (SELECT 1 AS id) DELETE FROM data.costs USING a WHERE costs.id=a.id",
        "WITH removed AS (DELETE FROM data.costs RETURNING id) SELECT id FROM removed",
        (
            "CREATE VIEW data.totals AS WITH a AS (SELECT amount FROM data.costs) "
            "SELECT sum(amount) FROM a"
        ),
    ],
)
def test_owner_statements_can_use_cte_query_sources(statement):
    from opendb.databases.sql_policy import validate_sql

    validate_sql(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO data.large VALUES (1,$1) RETURNING *",
        "INSERT INTO data.costs DEFAULT VALUES RETURNING id",
        "UPDATE data.costs SET amount=$1 WHERE id=$2 RETURNING id, amount",
        "DELETE FROM data.costs WHERE id=$1 RETURNING *",
    ],
)
def test_dml_returning_is_allowed_for_owners_only(statement):
    from opendb.databases.sql_policy import validate_sql

    validate_sql(statement)
    with pytest.raises(ValueError, match="Statement is not supported"):
        validate_sql(statement, readonly=True)


@pytest.mark.parametrize(
    "statement",
    [
        "WITH costs AS (SELECT 1) INSERT INTO costs DEFAULT VALUES",
        "WITH costs AS (SELECT 1) UPDATE costs SET amount=1",
        "WITH costs AS (SELECT 1) DELETE FROM costs",
        "WITH removed AS (DELETE FROM costs RETURNING *) SELECT * FROM removed",
        "CREATE VIEW totals AS WITH totals AS (SELECT 1) SELECT * FROM totals",
    ],
)
def test_cte_names_never_authorize_unqualified_write_targets(statement):
    from opendb.databases.sql_policy import validate_sql

    with pytest.raises(ValueError, match="explicitly qualified"):
        validate_sql(statement)
