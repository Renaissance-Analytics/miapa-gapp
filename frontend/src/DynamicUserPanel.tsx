import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { Button, Card } from '@particle-academy/react-fancy';
import { Screen, ScreenSystem, registerSchemaComponents } from '@particle-academy/fancy-screens';
import { IntakeWorkspace } from './IntakeWorkspace.js';
import { UserPanelModel, initialUserPanelState } from './user-panel.js';

const FactsAttempt = createContext(0);
function ClientFacts() { const attempt = useContext(FactsAttempt); return <IntakeWorkspace key={attempt} />; }
function WorkspaceGuide() {
  return <Card variant="outlined" sections="plain"><Card.Body><h3>Your workspace</h3>
    <p>Keep your conversation open while you work with client facts and their evidence.</p>
    <p>Complete Account setup in the conversation panel, then open Client facts to review the saved answers.</p>
    <p>Agent account connection still requires setup.</p>
  </Card.Body></Card>;
}
registerSchemaComponents({
  'Maipa.ClientFacts': ClientFacts,
  'Maipa.WorkspaceGuide': WorkspaceGuide,
  'Maipa.Stack': ({ children }) => <div className="work-view-stack">{children as React.ReactNode}</div>,
  'Maipa.Note': ({ text }) => <Card variant="outlined" sections="plain"><Card.Body>
    <span className="eyebrow">Agent note · verify against evidence</span><p>{String(text)}</p>
  </Card.Body></Card>,
});

export function DynamicUserPanel({ userId, factsAttempt }: { userId: string; factsAttempt: number }) {
  const model = useRef<UserPanelModel | null>(null);
  const [state, setState] = useState(initialUserPanelState);
  useEffect(() => {
    const current = new UserPanelModel(userId);
    model.current = current;
    const unsubscribe = current.subscribe(setState);
    return () => { unsubscribe(); current.dispose(); model.current = null; };
  }, [userId]);
  useEffect(() => { if (factsAttempt > 0) model.current?.navigate('client-facts'); }, [factsAttempt]);
  const active = state.views.find(view => view.id === state.active)!;
  return <FactsAttempt.Provider value={factsAttempt}><ScreenSystem>
    <nav className="work-view-controls" aria-label="Work views">{state.views.map(view => <Button key={view.id}
      size="sm" variant="ghost" aria-pressed={view.id === state.active} onClick={() => model.current?.navigate(view.id)}>{view.title}</Button>)}</nav>
    <Screen id={active.id} title={active.title} schema={active.schema} />
  </ScreenSystem></FactsAttempt.Provider>;
}
