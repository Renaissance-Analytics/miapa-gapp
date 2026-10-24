from django.urls import path

from . import api, builds, development
from .agent_accounts import views as agent_accounts
from .workspace import views as workspace
from .workspace import setup_views
from .intake import views as intake

urlpatterns = [
    path("", development.shell, name="development-shell"),
    path("assets/<path:path>", development.asset, name="development-asset"),
    path("api/health/", api.health, name="health"),
    path("api/version/", builds.version, name="version"),
    path("api/session/", api.session_state, name="session"),
    path("api/session/csrf/", workspace.csrf_bootstrap, name="session-csrf"),
    path("api/session/login/", workspace.session_login, name="session-login"),
    path("api/workspace/", workspace.workspace_state, name="workspace"),
    path('api/setup/', setup_views.first_run, name='first-run'),
    path('api/onboarding/', setup_views.account_setup, name='account-onboarding'),
    path("api/intake/engagements/", intake.engagements, name="intake-engagements"),
    path("api/intake/engagements/<uuid:engagement_id>/entities/", intake.entities, name="intake-entities"),
    path("api/intake/entities/<uuid:entity_id>/", intake.entity, name="intake-entity"),
    path("api/intake/entities/<uuid:entity_id>/facts/", intake.facts, name="intake-facts"),
    path("api/intake/entities/<uuid:entity_id>/facts/<slug:key>/history/", intake.history, name="intake-history"),
    path("api/session/logout/", api.session_logout, name="session-logout"),
    path("api/agent/accounts/", agent_accounts.accounts, name="agent-accounts"),
    path("api/agent/accounts/<uuid:account_id>/transport-checks/", agent_accounts.transport_checks, name="agent-transport-checks"),
]

handler400 = api.bad_request
handler403 = api.forbidden
handler404 = api.not_found
handler500 = api.server_error
