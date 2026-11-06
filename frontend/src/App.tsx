import { useEffect, useRef, useState } from 'react';
import { Button, Popover, initTheme, useTheme } from '@particle-academy/react-fancy';
import { readSession, themeStorageKey, type SessionState } from './api.js';
import { BuildUpdates } from './BuildUpdates.js';
import { updatesStorageKey } from './updates.js';
import { SignOutButton } from './Authentication.js';
import { LocalAccess } from './FirstRun.js';
import { AgentWorkspace } from './AgentWorkspace.js';
import { LayoutController, type LayoutState } from './layout.js';
import { PreferencesController, initialPreferences } from './preferences.js';

type ThemeChoice = 'system' | 'light' | 'dark';
/** Signed-in theme edits wait for saved preferences; signed-out choices are local only. */
export function appearanceLocked(session: SessionState, preferencesPhase: string) {
  return session.state === 'authenticated' && preferencesPhase !== 'ready';
}
/** The account menu's appearance choices; the menu itself is a Fancy Popover. */
export function AppearanceChoices({ preference, disabled, choose }: { preference: string; disabled: boolean; choose: (value: ThemeChoice) => void }) {
  return <div className="appearance" role="group" aria-label="Appearance">
    {(['system', 'light', 'dark'] as const).map(value => <Button key={value} size="sm" variant="ghost" id={`maipa-theme-${value}`}
      disabled={disabled} aria-pressed={preference === value} onClick={() => choose(value)}>
      {value === 'system' ? 'System' : value === 'light' ? 'Light' : 'Dark'}</Button>)}
  </div>;
}

export function Workspace({ session, refresh }: { session: SessionState; refresh: () => void }) {
  const identity = session.state === 'authenticated' ? `user.${session.userId}` : session.state;
  return <WorkspaceSession key={identity} session={session} refresh={refresh} />;
}

function WorkspaceSession({ session, refresh }: { session: SessionState; refresh: () => void }) {
  const theme = useTheme();
  const [headerHost,setHeaderHost]=useState<HTMLDivElement|null>(null);
  const [accountOpen,setAccountOpen]=useState(false);
  const [layout, setLayout] = useState<LayoutState>({ phase: session.state === 'authenticated' ? 'loading' : 'ready', split: 60 });
  const layoutController = useRef<LayoutController | null>(null);
  const [preferences, setPreferences] = useState(initialPreferences);
  const preferencesController = useRef<PreferencesController | null>(null);
  useEffect(() => {
    if (session.state !== 'authenticated') return;
    const controller = new LayoutController(session.userId, setLayout);
    layoutController.current = controller; void controller.load();
    return () => { controller.dispose(); layoutController.current = null; };
  }, [session.state === 'authenticated' ? session.userId : null]);
  useEffect(() => {
    if (session.state !== 'authenticated') return;
    const controller = new PreferencesController(session.userId, setPreferences);
    preferencesController.current = controller; void controller.load();
    return () => { controller.dispose(); preferencesController.current = null; };
  }, [session.state === 'authenticated' ? session.userId : null]);
  useEffect(() => initTheme({ storageKey: themeStorageKey(session) }), [session]);
  useEffect(() => {
    if (session.state === 'authenticated') theme.setPreference(preferences.theme);
  }, [session.state, preferences.theme]);
  return <div className="workspace agent-first" data-preferences-state={preferences.phase} data-layout-state={layout.phase}>
    <header className="app-header">
      <div className="brand"><span className="brand-mark" aria-hidden="true">m</span><h1>Maipa</h1></div>
      <div className="shell-controls">
        {session.state==='authenticated'&&<div className="header-shortcut-host" ref={setHeaderHost}/>}
        <BuildUpdates key={updatesStorageKey(session)} session={session} />
        {/* Published Fancy popover: closes on outside click and Escape, so it never hides the work panel. */}
        <div className="account-menu" data-open={accountOpen}>
          <Popover open={accountOpen} onOpenChange={setAccountOpen} placement="bottom-end">
            <Popover.Trigger><Button size="sm" variant="ghost" aria-label="Account and appearance">Account</Button></Popover.Trigger>
            <Popover.Content className="account-popover">
              <AppearanceChoices preference={theme.preference} disabled={appearanceLocked(session, preferences.phase)}
                choose={value => {
                  if (session.state === 'authenticated') void preferencesController.current?.save({ theme: value });
                  else theme.setPreference(value);
                }} />
              {session.state === 'authenticated' && <SignOutButton refresh={refresh} />}
            </Popover.Content>
          </Popover>
        </div>
      </div>
    </header>
    {session.state === 'authenticated' ? <>
      {(preferences.phase === 'conflict' || preferences.phase === 'unavailable') && <div className="shell-recovery" role="alert">
        Your saved appearance changed or could not be loaded. <Button size="sm" variant="ghost" onClick={() => void preferencesController.current?.load()}>Reload saved preferences</Button>
      </div>}
      {(layout.phase === 'conflict' || layout.phase === 'unavailable') && <div className="shell-recovery" role="alert">
        Your saved panel width changed or could not be loaded. <Button size="sm" variant="ghost" onClick={() => void layoutController.current?.load()}>Reload saved layout</Button>
      </div>}
      <AgentWorkspace userId={session.userId} headerHost={headerHost} layout={layout} onResize={split => layoutController.current?.change(split)}
        onResizeCommit={split => { void layoutController.current?.commit(split); }} />
    </> : <main className="local-access" aria-label="Conversation">
      {session.state === 'anonymous' ? <LocalAccess refresh={refresh} /> : <>
        <h2>{session.state === 'unavailable' ? 'Workspace unavailable' : 'Opening Maipa…'}</h2>
        {session.state === 'unavailable' && <Button onClick={refresh}>Try again</Button>}
      </>}
    </main>}
  </div>;
}

export default function App() {
  const [session, setSession] = useState<SessionState>({ state: 'checking' });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true; setSession({ state: 'checking' });
    void readSession().then(value => { if (current) setSession(value); });
    return () => { current = false; };
  }, [attempt]);
  return <Workspace session={session} refresh={() => setAttempt(value => value + 1)} />;
}
