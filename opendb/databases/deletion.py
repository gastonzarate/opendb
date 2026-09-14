"""Owner-authorized physical deletion with a durable control-plane tombstone."""

import re

from django.contrib.auth import get_user_model
from django.db import transaction
from psycopg import sql

from .connections import require_owner
from .lifecycle import database_lock
from .models import AccessRole


def _owned_resources(admin, db):
    """Validate every candidate before destruction; never infer ownership by prefix."""
    marker = f"opendb:{db.id}"
    database = admin.execute(
        "SELECT pg_get_userbyid(datdba), shobj_description(oid, 'pg_database') "
        "FROM pg_database WHERE datname=%s",
        (db.database_name,),
    ).fetchone()
    admin_user = admin.execute("SELECT current_user").fetchone()[0]
    if database and database != (admin_user, marker):
        msg = "Resource ownership conflict"
        raise ValueError(msg)

    groups = {role.pg_name for role in AccessRole.objects.filter(database=db)}
    guest_pattern = rf"odb_guest_{db.id.hex}_[0-9]+"
    rows = admin.execute(
        "SELECT rolname, shobj_description(oid, 'pg_authid'), "
        "rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls "
        "FROM pg_roles WHERE rolname=%s OR rolname=ANY(%s) OR rolname ~ %s "
        "OR shobj_description(oid, 'pg_authid')=%s",
        (db.role_name, list(groups), f"^{guest_pattern}$", marker),
    ).fetchall()
    roles = []
    legacy = []
    for name, comment, elevated in rows:
        scoped = (
            name == db.role_name
            or name in groups
            or re.fullmatch(guest_pattern, name)
            or re.fullmatch(r"odb_group_[0-9a-f]{32}", name)
        )
        if not scoped or elevated or comment not in {None, marker}:
            msg = "Resource ownership conflict"
            raise ValueError(msg)
        if comment is None:
            if not database or not _legacy_role_is_owned(admin, db, groups, name):
                msg = "Resource ownership conflict"
                raise ValueError(msg)
            legacy.append(name)
        roles.append(name)
    # A present data database must still have its marked owner role. If the
    # database is already gone, missing roles are normal partial-delete retries.
    if database and db.role_name not in roles:
        msg = "Resource ownership conflict"
        raise ValueError(msg)
    # Reconcile only after the entire resource set passes preflight. Persist all
    # markers atomically before DROP so retries after database removal stay safe.
    with admin.transaction():
        for name in legacy:
            admin.execute(
                sql.SQL("COMMENT ON ROLE {} IS {}").format(
                    sql.Identifier(name), sql.Literal(marker)
                )
            )
    return database, roles


def _legacy_role_is_owned(admin, db, groups, name):
    """Require control identity plus exclusive, read-only PostgreSQL scope."""
    oid, login = admin.execute(
        "SELECT oid, rolcanlogin FROM pg_roles WHERE rolname=%s", (name,)
    ).fetchone()
    database_oid = admin.execute(
        "SELECT oid FROM pg_database WHERE datname=%s", (db.database_name,)
    ).fetchone()[0]
    dependencies = admin.execute(
        "SELECT dbid, classid='pg_database'::regclass, objid, deptype "
        "FROM pg_shdepend WHERE refclassid='pg_authid'::regclass AND refobjid=%s",
        (oid,),
    ).fetchall()
    # Any ownership, policy, foreign-database or other shared-object dependency
    # is incompatible with a legacy OpenDB read role. Never DROP OWNED elsewhere.
    if not dependencies or any(
        kind != "a"
        or not (
            dbid == database_oid
            or (dbid == 0 and is_database and object_id == database_oid)
        )
        for dbid, is_database, object_id, kind in dependencies
    ):
        return False
    memberships = admin.execute(
        "SELECT parent.rolname, member.rolname FROM pg_auth_members m "
        "JOIN pg_roles parent ON parent.oid=m.roleid "
        "JOIN pg_roles member ON member.oid=m.member "
        "WHERE m.roleid=%s OR m.member=%s",
        (oid, oid),
    ).fetchall()
    guest_pattern = rf"odb_guest_{db.id.hex}_([0-9]+)"
    if name in groups:
        return not login and all(
            parent == name and re.fullmatch(guest_pattern, member)
            for parent, member in memberships
        )
    guest = re.fullmatch(guest_pattern, name)
    if not guest or not login:
        return False
    if not get_user_model().objects.filter(pk=int(guest[1])).exists():
        return False
    if any(member != name or parent not in groups for parent, member in memberships):
        return False
    # A guest's name is insufficient: require its explicit CONNECT ACL granted
    # by this administrator, even when its last application assignment was revoked.
    return bool(
        admin.execute(
            "SELECT 1 FROM pg_database d, LATERAL aclexplode(d.datacl) a "
            "WHERE d.oid=%s AND a.grantee=%s AND a.privilege_type='CONNECT' "
            "AND a.grantor=(SELECT oid FROM pg_roles WHERE rolname=current_user)",
            (database_oid, oid),
        ).fetchone()
    )


def delete_personal_database(actor_id, database_id):
    db = require_owner(actor_id, database_id)
    with database_lock(db.id) as admin:
        db = require_owner(actor_id, database_id)
        if db.status == "deleted":
            return db
        db.status, db.error_code = "deleting", ""
        db.save(update_fields=["status", "error_code"])
        try:
            database, roles = _owned_resources(admin, db)
            if database:
                # FORCE disconnects owner, guest, worker and idle sessions and
                # drops all data, catalog and vector schemas as one database.
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(db.database_name)
                    )
                )
            for role in roles:
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
            # Keep role metadata until physical cleanup finishes so retries retain
            # exact role identities. Removing AccessRole cascades assignments/grants.
            with transaction.atomic():
                AccessRole.objects.filter(database=db).delete()
                db.status, db.error_code = "deleted", ""
                db.onboarding_completed = False
                db.save(update_fields=["status", "error_code", "onboarding_completed"])
        except Exception:
            db.status, db.error_code = "delete_failed", "delete_failed"
            db.save(update_fields=["status", "error_code"])
            raise
    return db
