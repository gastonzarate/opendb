from django.urls import path

from . import api
from . import web

app_name = "gateway"
urlpatterns = [
    path("bootstrap/", web.bootstrap, name="bootstrap"),
    path("session/", api.session, name="session"),
    path("actions/<str:action>/", api.action, name="action"),
]
