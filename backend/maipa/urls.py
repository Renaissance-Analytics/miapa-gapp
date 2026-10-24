from django.urls import path

from . import api

urlpatterns = [
    path("api/health/", api.health, name="health"),
    path("api/session/", api.session_state, name="session"),
    path("api/session/logout/", api.session_logout, name="session-logout"),
]

handler400 = api.bad_request
handler403 = api.forbidden
handler404 = api.not_found
handler500 = api.server_error
