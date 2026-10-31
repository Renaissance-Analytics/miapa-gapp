import { createContext, useContext } from 'react';
import { Screen, ScreenSystem, registerSchemaComponents, type ScreenSchema } from '@particle-academy/fancy-screens';
import { Callout, ContentRenderer, Heading, renderMarkdown } from '@particle-academy/react-fancy';
import { IntakeWorkspace } from './IntakeWorkspace.js';
import { ClientCatalog } from './ClientCatalog.js';
import { AdminUsers } from './AdminUsers.js';
import type { Composition } from './agent-chat.js';
import { CHAT_PROSE } from './content-policy.js';
import { ClientTable, type ClientColumn } from './ClientTable.js';
import type { FormBinding, NativeFormActivity } from './shared-form.js';

const Principal = createContext('');
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
});

/** The exact server-controlled schema is shared by human controls and agent tools. */
export function AgentSurface({ userId, ui, live, refreshRevision, activity }: { userId: string; ui: Pick<Composition,'composition'>;
  live?: Composition | null; refreshRevision?: number; activity?: NativeFormActivity }) {
  if (!ui.composition) return null;
  let componentId: string | undefined;
  function visit(node: ScreenSchema) {
    if (node.type === 'Maipa.ClientCatalog') componentId = node.id;
    for (const child of node.children ?? []) if (typeof child !== 'string') visit(child);
  }
  // A saved offline view does not inherit authority from a different live panel.
  if (live?.composition && JSON.stringify(live.composition) === JSON.stringify(ui.composition)) visit(ui.composition.schema);
  const binding = live && componentId ? { userId, accountId: live.account_id, leaseId: live.id, componentId } : undefined;
  return <Principal.Provider value={userId}><SharedForm.Provider value={{ binding, refreshRevision, activity }}><ScreenSystem>
    <Screen id={`composition-user-${userId}`} title={ui.composition.title} schema={ui.composition.schema} />
  </ScreenSystem></SharedForm.Provider></Principal.Provider>;
}
