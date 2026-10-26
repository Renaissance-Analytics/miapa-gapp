from django.urls import path

from . import api, builds, development
from .agent_accounts import views as agent_accounts
from .agent_accounts.ui_views import composition as agent_ui
from .workspace import views as workspace
from .workspace import setup_views
from .workspace.layout import personal_layout
from .workspace.preferences import personal_preferences
from .workspace.access import users as admin_users
from .intake import views as intake
from .intake.catalog_views import clients as client_catalog
from .policies import views as policies

urlpatterns = [
    path("", development.shell, name="development-shell"),
    path("assets/<path:path>", development.asset, name="development-asset"),
    path("api/health/", api.health, name="health"),
    path("api/version/", builds.version, name="version"),
    path("api/session/", api.session_state, name="session"),
    path("api/session/csrf/", workspace.csrf_bootstrap, name="session-csrf"),
    path("api/session/login/", workspace.session_login, name="session-login"),
    path("api/workspace/", workspace.workspace_state, name="workspace"),
    path('api/admin/users/', admin_users, name='admin-users'),
    path('api/admin/users/<int:target>/', admin_users, name='admin-user'),
    path('api/intake/catalog/', client_catalog, name='client-catalog'),
    path('api/workspace/layout/', personal_layout, name='workspace-layout'),
    path('api/workspace/preferences/', personal_preferences, name='workspace-preferences'),
    path('api/setup/', setup_views.first_run, name='first-run'),
    path('api/onboarding/', setup_views.account_setup, name='account-onboarding'),
    path('api/policies/entities/<uuid:entity_id>/', policies.entity_authority, name='entity-authority'),
    path('api/policies/grants/<uuid:grant_id>/revoke/', policies.revoke_grant, name='authority-revoke'),
    path('api/policies/grants/<uuid:grant_id>/delegate/', policies.delegate_grant, name='authority-delegate'),
    path("api/intake/engagements/", intake.engagements, name="intake-engagements"),
    path("api/intake/engagements/<uuid:engagement_id>/entities/", intake.entities, name="intake-entities"),
    path("api/intake/entities/<uuid:entity_id>/", intake.entity, name="intake-entity"),
    path("api/intake/entities/<uuid:entity_id>/facts/", intake.facts, name="intake-facts"),
    path("api/intake/entities/<uuid:entity_id>/facts/<slug:key>/history/", intake.history, name="intake-history"),
    path("api/session/logout/", api.session_logout, name="session-logout"),
    path("api/agent/accounts/", agent_accounts.accounts, name="agent-accounts"),
    path('api/agent/accounts/<uuid:account_id>/ui/<uuid:identity>/', agent_ui, name='agent-ui-composition'),
    path("api/agent/accounts/<uuid:account_id>/transport-checks/", agent_accounts.transport_checks, name="agent-transport-checks"),
]

handler400 = api.bad_request
handler403 = api.forbidden
handler404 = api.not_found
handler500 = api.server_error
