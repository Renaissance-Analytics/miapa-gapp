import { createContext, useContext } from 'react';
import { Screen, ScreenSystem, registerSchemaComponents } from '@particle-academy/fancy-screens';
import { IntakeWorkspace } from './IntakeWorkspace.js';
import { ClientCatalog } from './ClientCatalog.js';
import { AdminUsers } from './AdminUsers.js';
import type { Composition } from './agent-chat.js';

const Principal = createContext('');
registerSchemaComponents({
  'Maipa.Stack': ({ children }) => <div className="composition-stack">{children as React.ReactNode}</div>,
  'Maipa.Note': ({ text }) => <div className="agent-commentary"><small>Maipa · commentary</small><p>{String(text)}</p></div>,
  'Maipa.ClientFacts': () => <IntakeWorkspace />,
  'Maipa.ClientCatalog': () => <ClientCatalog userId={useContext(Principal)} />,
  'Maipa.AdminUsers': () => <AdminUsers userId={useContext(Principal)} />,
});

/** The exact server-controlled schema is shared by human controls and agent tools. */
export function AgentSurface({ userId, ui }: { userId: string; ui: Composition }) {
  if (!ui.composition) return null;
  return <Principal.Provider value={userId}><ScreenSystem>
    <Screen id={`composition-user-${userId}`} title={ui.composition.title} schema={ui.composition.schema} />
  </ScreenSystem></Principal.Provider>;
}
