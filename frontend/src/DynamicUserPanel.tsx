import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { Button, Card } from '@particle-academy/react-fancy';
import { Screen, ScreenSystem, registerSchemaComponents } from '@particle-academy/fancy-screens';
import { IntakeWorkspace } from './IntakeWorkspace.js';
import { AdminUsers } from './AdminUsers.js';
import { canAdminister } from './admin-users.js';
import { UserPanelModel, initialUserPanelState } from './user-panel.js';
import type { BuiltinView, PreferencesState } from './preferences.js';

const FactsAttempt = createContext(0);
const Principal = createContext('');
function Administration() { return <AdminUsers userId={useContext(Principal)} />; }
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
  'Maipa.AdminUsers': Administration,
  'Maipa.Stack': ({ children }) => <div className="work-view-stack">{children as React.ReactNode}</div>,
  'Maipa.Note': ({ text }) => <Card variant="outlined" sections="plain"><Card.Body>
    <span className="eyebrow">Agent note · verify against evidence</span><p>{String(text)}</p>
  </Card.Body></Card>,
});

export function DynamicUserPanel({ userId, factsAttempt, preferences = { phase: 'ready', theme: 'system', active_view: 'client-facts' }, onSelectBuiltin }:
  { userId: string; factsAttempt: number; preferences?: PreferencesState; onSelectBuiltin?: (id: BuiltinView) => boolean }) {
  const model = useRef<UserPanelModel | null>(null);
  const navigation = useRef({ preferences, onSelectBuiltin });
  navigation.current = { preferences, onSelectBuiltin };
  const previousPreferences = useRef(preferences);
  const [state, setState] = useState(initialUserPanelState);
  useEffect(() => {
    const current = new UserPanelModel(userId, id => navigation.current.preferences.phase === 'ready'
      && (navigation.current.onSelectBuiltin?.(id as BuiltinView) ?? true));
    model.current = current;
    const unsubscribe = current.subscribe(setState);
    let active = true;
    // Cosmetic discovery only. Every admin read/write checks fresh server authority.
    void canAdminister().then(allowed => { if (active && allowed) current.enableAdministration(); });
    return () => { active = false; unsubscribe(); current.dispose(); model.current = null; };
  }, [userId]);
  useEffect(() => {
    const previous = previousPreferences.current;
    previousPreferences.current = preferences;
    if (preferences.phase === 'ready' && (previous.phase === 'loading' || previous.active_view !== preferences.active_view))
      model.current?.restoreBuiltin(preferences.active_view);
  }, [preferences.phase, preferences.active_view]);
  useEffect(() => {
    if (factsAttempt > 0 && model.current) {
      try { model.current.navigate('client-facts'); }
      catch { model.current.restoreBuiltin('client-facts'); }
    }
  }, [factsAttempt]);
  const active = state.views.find(view => view.id === state.active)!;
  return <Principal.Provider value={userId}><FactsAttempt.Provider value={factsAttempt}><ScreenSystem>
    <nav className="work-view-controls" aria-label="Work views">{state.views.map(view => <Button key={view.id}
      size="sm" variant="ghost" disabled={preferences.phase !== 'ready'} aria-pressed={view.id === state.active}
      onClick={() => { try { model.current?.navigate(view.id); } catch { /* A save may have started before this event reached React. */ } }}>{view.title}</Button>)}</nav>
    <Screen id={active.id} title={active.title} schema={active.schema} />
  </ScreenSystem></FactsAttempt.Provider></Principal.Provider>;
}
