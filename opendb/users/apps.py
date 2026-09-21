from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class UsersConfig(AppConfig):
    name = "opendb.users"
    verbose_name = _("Users")

    def ready(self):
        """
        Override this method in subclasses to run code when Django starts.
        """
        from allauth.account.signals import user_signed_up  # noqa: PLC0415

        from ._signals import provision_local_signup_receiver  # noqa: PLC0415

        user_signed_up.connect(provision_local_signup_receiver)
