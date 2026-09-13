from django.core.exceptions import PermissionDenied


class DatabaseAccessDenied(PermissionDenied):
    pass


class DatabaseNotReady(ValueError):  # noqa: N818 - public contract
    pass
