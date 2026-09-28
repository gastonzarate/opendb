import psycopg
import pytest

from opendb.databases.services import ADMIN_CONNECTION_ACTIONS
from opendb.databases.services import _database_error_message


class _AuthFailed(psycopg.OperationalError):
    sqlstate = "28P01"


class _Transport(psycopg.OperationalError):
    sqlstate = None


@pytest.mark.parametrize("action", sorted(ADMIN_CONNECTION_ACTIONS))
@pytest.mark.parametrize("exc", [_AuthFailed("x"), _Transport("x")])
def test_admin_connection_actions_point_at_admin_dsn(action, exc):
    message = _database_error_message(exc, action)
    assert "OPENDB_ADMIN_DSN" in message
    assert "x" not in message.split(":", 2)[2].replace("OperationalError", "")


@pytest.mark.parametrize("exc", [_AuthFailed("x"), _Transport("x")])
def test_owner_connection_actions_do_not_blame_admin_dsn(exc):
    assert "OPENDB_ADMIN_DSN" not in _database_error_message(exc, "query")
