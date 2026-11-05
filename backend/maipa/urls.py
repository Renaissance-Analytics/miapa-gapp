from django.urls import path

from . import api, builds, development
from .agent_accounts import views as agent_accounts
from .agent_accounts.ui_views import composition as agent_ui
from .agent_accounts import chat_views
from .agent_accounts.form_views import form as agent_form
from .workspace import views as workspace
from .workspace import setup_views
from .workspace.layout import personal_layout
from .workspace.preferences import personal_preferences
from .workspace.personal_views import personal_views
from .workspace.access import users as admin_users
from .intake import views as intake
from .intake.catalog_views import clients as client_catalog
from .policies import views as policies
from .workflows import views as workflows

from .agent_accounts.draft_views import drafts as chat_drafts
from .agent_accounts.settings_view import settings as agent_settings
from .agent_accounts.metadata import metadata as agent_metadata

urlpatterns = [
    path('api/agent/accounts/<uuid:account_id>/metadata/', agent_metadata, name='agent-account-metadata'),
    path("api/agent/settings/",agent_settings,name="agent-settings"),
    path("api/agent/drafts/unselected/",chat_drafts,name="chat-unselected-draft"),
    path("api/agent/drafts/<uuid:account_id>/",chat_drafts,name="chat-owned-draft"),
    path("", development.shell, name="development-shell"),
    path("assets/<path:path>", development.asset, name="development-asset"),
    path("api/health/", api.health, name="health"),
    path("api/version/", builds.version, name="version"),
    path("api/session/", api.session_state, name="session"),
    path("api/session/csrf/", workspace.csrf_bootstrap, name="session-csrf"),
    path("api/session/login/", workspace.session_login, name="session-login"),
    path("api/workspace/", workspace.workspace_state, name="workspace"),
    path('api/workflows/runs/<uuid:run_id>/human/',workflows.pending,name='workflow-human-gate'),
    path('api/workflows/runs/<uuid:run_id>/gates/<uuid:gate_id>/response/',workflows.response,name='workflow-human-response'),
    path('api/workflows/drafts/',workflows.authoring,name='workflow-drafts'),
    path('api/workflows/drafts/<uuid:draft_id>/',workflows.draft,name='workflow-draft'),
    path('api/workflows/drafts/<uuid:draft_id>/publish/',workflows.publish_draft,name='workflow-publish-draft'),
    path('api/workflows/drafts/<uuid:draft_id>/execution/',workflows.execution_draft,name='workflow-execution'),
    path('api/workflows/drafts/<uuid:draft_id>/selection/',workflows.select_run,name='workflow-run-selection'),
    path('api/workflows/drafts/<uuid:draft_id>/agent-authority/<uuid:account_id>/<uuid:ui_id>/',workflows.agent_authority,name='workflow-agent-authority'),
    path('api/workflows/runs/<uuid:run_id>/',workflows.execution_run,name='workflow-run'),
    path('api/workflows/runs/<uuid:run_id>/cancel/',workflows.execution_control,{'operation':'cancel'},name='workflow-cancel'),
    path('api/workflows/runs/<uuid:run_id>/resume/',workflows.execution_control,{'operation':'resume'},name='workflow-resume'),
    path('api/admin/users/', admin_users, name='admin-users'),
    path('api/admin/users/<int:target>/', admin_users, name='admin-user'),
    path('api/intake/catalog/', client_catalog, name='client-catalog'),
    path('api/workspace/layout/', personal_layout, name='workspace-layout'),
    path('api/workspace/preferences/', personal_preferences, name='workspace-preferences'),
    path('api/workspace/views/', personal_views, name='workspace-personal-views'),
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
    path('api/agent/chat/', chat_views.conversation, name='agent-chat'),
    path('api/agent/chat/stream/', chat_views.stream, name='agent-chat-stream'),
    path('api/agent/accounts/<uuid:account_id>/ui/<uuid:identity>/', agent_ui, name='agent-ui-composition'),
    path('api/agent/accounts/<uuid:account_id>/ui/<uuid:identity>/forms/<slug:component_id>/',agent_form,name='agent-shared-form'),
    path("api/agent/accounts/<uuid:account_id>/transport-checks/", agent_accounts.transport_checks, name="agent-transport-checks"),
]

handler400 = api.bad_request
handler403 = api.forbidden
handler404 = api.not_found
handler500 = api.server_error
