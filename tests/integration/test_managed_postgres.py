"""Managed PostgreSQL administrators lack the unrestricted superuser bypass."""

from uuid import uuid4

import pytest
from psycopg import sql

pytestmark = pytest.mark.django_db(transaction=True)


def test_provisioner_inherits_owner_but_owner_cannot_assume_admin(admin_conn):
    from opendb.databases.provisioning import _grant_provisioner_membership

    administrator = "odb_managed_admin_" + uuid4().hex
    owner = "odb_managed_owner_" + uuid4().hex
    schema = "managed_" + uuid4().hex
    admin_conn.execute(
        sql.SQL("CREATE ROLE {} NOLOGIN CREATEROLE").format(
            sql.Identifier(administrator)
        )
    )
    try:
        admin_conn.execute(
            sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                sql.Identifier(admin_conn.info.dbname), sql.Identifier(administrator)
            )
        )
        admin_conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(administrator)))
        admin_conn.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner))
        )
        _grant_provisioner_membership(admin_conn, owner)
        admin_conn.execute(
            sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                sql.Identifier(schema), sql.Identifier(owner)
            )
        )
        assert admin_conn.execute(
            "SELECT pg_has_role(current_user,%s,'USAGE'),"
            "pg_has_role(current_user,%s,'SET')",
            (owner, owner),
        ).fetchone() == (True, True)
        assert admin_conn.execute(
            "SELECT pg_has_role(%s,%s,'SET')", (owner, administrator)
        ).fetchone() == (False,)
        # Idempotent retries preserve the same one-way administration relationship.
        _grant_provisioner_membership(admin_conn, owner)
    finally:
        admin_conn.execute("RESET ROLE")
        admin_conn.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema))
        )
        admin_conn.execute(
            sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(owner))
        )
        admin_conn.execute(
            sql.SQL("DROP OWNED BY {}").format(sql.Identifier(administrator))
        )
        admin_conn.execute(
            sql.SQL("DROP ROLE {}").format(sql.Identifier(administrator))
        )
