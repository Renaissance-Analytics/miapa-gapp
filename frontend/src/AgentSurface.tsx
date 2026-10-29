import { createContext, useContext } from 'react';
import { Screen, ScreenSystem, registerSchemaComponents } from '@particle-academy/fancy-screens';
import { Callout, ContentRenderer, Heading, renderMarkdown } from '@particle-academy/react-fancy';
import { IntakeWorkspace } from './IntakeWorkspace.js';
import { ClientCatalog } from './ClientCatalog.js';
import { AdminUsers } from './AdminUsers.js';
import type { Composition } from './agent-chat.js';
import { CHAT_PROSE } from './content-policy.js';
import { ClientTable, type ClientColumn } from './ClientTable.js';

const Principal = createContext('');
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
  'Maipa.ClientCatalog': () => <ClientCatalog userId={useContext(Principal)} />,
  'Maipa.AdminUsers': () => <AdminUsers userId={useContext(Principal)} />,
});

/** The exact server-controlled schema is shared by human controls and agent tools. */
export function AgentSurface({ userId, ui }: { userId: string; ui: Pick<Composition,'composition'> }) {
  if (!ui.composition) return null;
  return <Principal.Provider value={userId}><ScreenSystem>
    <Screen id={`composition-user-${userId}`} title={ui.composition.title} schema={ui.composition.schema} />
  </ScreenSystem></Principal.Provider>;
}
