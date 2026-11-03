import { createContext, lazy, Suspense, useContext } from 'react';
import { Screen, ScreenSystem, registerSchemaComponents, type ScreenSchema } from '@particle-academy/fancy-screens';
import { Callout, ContentRenderer, Heading, renderMarkdown } from '@particle-academy/react-fancy';
import { IntakeWorkspace } from './IntakeWorkspace.js';
import { ClientCatalog } from './ClientCatalog.js';
import { AdminUsers } from './AdminUsers.js';
import {AgentSettings} from './AgentSettings.js';
import type {SettingsControls} from './agent-settings.js';
import type { Composition } from './agent-chat.js';
import { CHAT_PROSE } from './content-policy.js';
import { ClientTable, type ClientColumn } from './ClientTable.js';
import type { FormBinding, NativeFormActivity } from './shared-form.js';
import type {WorkflowActivity} from './shared-workflow.js';
import type {WorkflowConnection} from './workflow-permissions.js';

const Principal = createContext('');
const ConnectionSettings=createContext<SettingsControls|null>(null);
const SharedWorkflowEditor = lazy(() => import('./WorkflowEditor.js').then(module => ({default:module.WorkflowEditor})));
const SharedWorkflowActivity=createContext<WorkflowActivity|undefined>(undefined);
const WorkflowConnectionContext=createContext<WorkflowConnection|undefined>(undefined);
const SharedForm = createContext<{ binding?: FormBinding; refreshRevision?: number; activity?: NativeFormActivity }>({});
function SharedClientCatalog() {
  const shared = useContext(SharedForm), userId = useContext(Principal);
  return <ClientCatalog key={shared.binding?.leaseId ?? 'offline'} userId={userId} {...shared} />;
}
registerSchemaComponents({
  'Maipa.Stack': ({ children }) => <div className="composition-stack">{children as React.ReactNode}</div>,
  'Maipa.Note': ({ text }) => <div className="agent-commentary"><small>Maipa · commentary</small>
    <ContentRenderer value={renderMarkdown(String(text), CHAT_PROSE)} format="html" className="maipa-content" /></div>,
  'Maipa.Heading': ({ text }) => <Heading as="h3" size="md" weight="semibold">{String(text)}</Heading>,
  'Maipa.Callout': ({ text, tone }) => <Callout className="composition-callout" role="note"
    color={tone === 'attention' ? 'amber' : tone === 'info' ? 'blue' : 'zinc'}><small>Maipa · commentary</small>
    <ContentRenderer value={renderMarkdown(String(text), CHAT_PROSE)} format="html" className="maipa-content" /></Callout>,
  'Maipa.ClientTable': ({ columns }) => <ClientTable userId={useContext(Principal)} columns={columns as ClientColumn[]} />,
  'Maipa.ClientFacts': () => <IntakeWorkspace />,
  'Maipa.ClientCatalog': SharedClientCatalog,
  'Maipa.AdminUsers': () => <AdminUsers userId={useContext(Principal)} />,
  'Maipa.AgentSettings': () => <AgentSettings userId={useContext(Principal)} controls={useContext(ConnectionSettings)} />,
  'Maipa.Workflow': ({draft_id}) => {
    const userId=useContext(Principal),draftId=String(draft_id);
    return <Suspense fallback={<span role="status">Loading workflow…</span>}>
      <SharedWorkflowEditor key={`${userId}:${draftId}`} userId={userId} draftId={draftId} activity={useContext(SharedWorkflowActivity)} connection={useContext(WorkflowConnectionContext)} />
    </Suspense>;
  },
});

/** The exact server-controlled schema is shared by human controls and agent tools. */
export function AgentSurface({ userId, ui, live, refreshRevision, activity,workflowActivity,settings }: { userId: string; ui: Pick<Composition,'composition'>;
  live?: Composition | null; refreshRevision?: number; activity?: NativeFormActivity;workflowActivity?:WorkflowActivity;settings?:SettingsControls }) {
  if (!ui.composition) return null;
  let componentId: string | undefined,workflowVisible=false;
  function visit(node: ScreenSchema) {
    if (node.type === 'Maipa.ClientCatalog') componentId = node.id;
    if (node.type === 'Maipa.Workflow') workflowVisible=true;
    for (const child of node.children ?? []) if (typeof child !== 'string') visit(child);
  }
  // A saved offline view does not inherit authority from a different live panel.
  if (live?.composition && JSON.stringify(live.composition) === JSON.stringify(ui.composition)) visit(ui.composition.schema);
  const binding = live && componentId ? { userId, accountId: live.account_id, leaseId: live.id, componentId } : undefined;
  const workflowConnection=live&&workflowVisible?{accountId:live.account_id,leaseId:live.id}:undefined;
  return <Principal.Provider value={userId}><ConnectionSettings.Provider value={settings??null}><SharedForm.Provider value={{ binding, refreshRevision, activity }}><SharedWorkflowActivity.Provider value={live?.id===workflowActivity?.ui_id?workflowActivity:undefined}><WorkflowConnectionContext.Provider value={workflowConnection}><ScreenSystem>
    <Screen id={`composition-user-${userId}`} title={ui.composition.title} schema={ui.composition.schema} />
  </ScreenSystem></WorkflowConnectionContext.Provider></SharedWorkflowActivity.Provider></SharedForm.Provider></ConnectionSettings.Provider></Principal.Provider>;
}
