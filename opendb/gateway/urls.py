from django.urls import path

from . import api
from . import web

app_name = "gateway"
urlpatterns = [
    path("bootstrap/", web.bootstrap, name="bootstrap"),
    path("session/", api.session, name="session"),
    path("actions/<str:action>/", api.action, name="action"),
    path(
        "personal-access-tokens/",
        api.personal_access_tokens,
        name="personal_access_tokens",
    ),
    path(
        "personal-access-tokens/create/",
        api.create_personal_access_token,
        name="create_personal_access_token",
    ),
    path(
        "personal-access-tokens/<int:token_id>/revoke/",
        api.revoke_personal_access_token,
        name="revoke_personal_access_token",
    ),
]
