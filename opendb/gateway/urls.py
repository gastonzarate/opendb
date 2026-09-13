from django.urls import path

from . import api

app_name = "gateway"
urlpatterns = [
    path("session/", api.session, name="session"),
    path("actions/<str:action>/", api.action, name="action"),
]
