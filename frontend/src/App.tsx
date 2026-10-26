import { useEffect, useRef, useState } from "react";
import { Button, Card, initTheme, ResizablePanels, useTheme } from "@particle-academy/react-fancy";
import { readSession, themeStorageKey, type SessionState } from "./api.js";
import { BuildUpdates } from "./BuildUpdates.js";
import { updatesStorageKey } from "./updates.js";
import { SignOutButton } from "./Authentication.js";
import { LocalAccess } from './FirstRun.js';
import { AccountSetupChat } from './AccountSetupChat.js';
import { DynamicUserPanel } from './DynamicUserPanel.js';
import { LayoutController, type LayoutState } from './layout.js';
import { PreferencesController, initialPreferences, type PersonalChoices } from './preferences.js';

export function Workspace({ session, refresh }: { session: SessionState; refresh: () => void }) {
  const identity = session.state === 'authenticated' ? `user.${session.userId}` : session.state;
  return <WorkspaceSession key={identity} session={session} refresh={refresh} />;
}

function WorkspaceSession({ session, refresh }: { session: SessionState; refresh: () => void }) {
  const theme = useTheme();
  const [layout, setLayout] = useState<LayoutState>({ phase: session.state === 'authenticated' ? 'loading' : 'ready', split: 28 });
  const layoutController = useRef<LayoutController | null>(null);
  const [preferences, setPreferences] = useState(initialPreferences);
  const preferencesController = useRef<PreferencesController | null>(null);
  const [pane, setPane] = useState<"agent" | "work">("work");
  const [narrow, setNarrow] = useState(false);
  const [factsAttempt, setFactsAttempt] = useState(0);

  useEffect(() => {
    if (session.state !== 'authenticated') return;
    const controller = new LayoutController(session.userId, setLayout);
    layoutController.current = controller;
    void controller.load();
    return () => { controller.dispose(); layoutController.current = null; };
  }, [session.state === 'authenticated' ? session.userId : null]);

  useEffect(() => {
    if (session.state !== 'authenticated') return;
    const controller = new PreferencesController(session.userId, setPreferences);
    preferencesController.current = controller;
    void controller.load();
    return () => { controller.dispose(); preferencesController.current = null; };
  }, [session.state === 'authenticated' ? session.userId : null]);

  function savePreferences(changes: Partial<PersonalChoices>): boolean {
    const controller = preferencesController.current;
    if (!controller || controller.current.phase !== 'ready') return false;
    void controller.save(changes);
    return true;
  }

  function changeSplit(split: number) {
    if (session.state === 'authenticated') layoutController.current?.change(split);
    else setLayout({ phase: 'ready', split });
  }

  useEffect(() => initTheme({ storageKey: themeStorageKey(session) }), [session]);
  useEffect(() => {
    if (session.state === 'authenticated') theme.setPreference(preferences.theme);
  }, [session.state, preferences.theme]);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 700px)");
    const changed = () => setNarrow(query.matches);
    changed();
    query.addEventListener("change", changed);
    return () => query.removeEventListener("change", changed);
  }, []);

  const failed = session.state === "unavailable";
  const anonymous = session.state === "anonymous";
  const status = session.state === "checking" ? "Checking workspace" : failed ? "Unavailable" : anonymous ? "Sign in required" : "Signed in";

  return <div className={`workspace show-${pane}`}>
    <header className="app-header">
      <div className="brand"><span className="brand-mark" aria-hidden="true">m</span><div><h1>Maipa</h1><p>My AI Professional Accountant</p></div></div>
      <div className="appearance" role="group" aria-label="Appearance">
        {(["system", "light", "dark"] as const).map(value => <Button key={value} size="sm" variant="ghost"
          id={`maipa-theme-${value}`} disabled={session.state === 'authenticated' && preferences.phase !== 'ready'}
          aria-pressed={theme.preference === value} onClick={() => {
            if (session.state === 'authenticated') savePreferences({ theme: value });
            else theme.setPreference(value);
          }}>
          {value === "system" ? "System" : value === "light" ? "Light" : "Dark"}
        </Button>)}
      </div>
    </header>
    <div className="mobile-panes" role="group" aria-label="Visible panel">
      <Button variant="ghost" aria-pressed={pane === "agent"} onClick={() => setPane("agent")}>Conversation</Button>
      <Button variant="ghost" aria-pressed={pane === "work"} onClick={() => setPane("work")}>Work area</Button>
    </div>
    <ResizablePanels className="workspace-panels" value={layout.split} onValueChange={changeSplit}
      onValueCommit={split => { void layoutController.current?.commit(split); }} min={22} max={45}
      disabled={session.state === 'authenticated' && layout.phase !== 'ready'}
      label="Resize conversation and work area">
      <section className="agent-panel" data-pane="agent" aria-label="Conversation" inert={narrow && pane !== "agent"}>
        <div className="panel-heading"><span className="eyebrow">Your financial copilot</span><h2>Conversation</h2></div>
        {session.state === 'authenticated' ? <AccountSetupChat key={session.userId} onOpenFacts={() => { setFactsAttempt(value => value + 1); setPane('work'); }} /> : <div className="agent-empty"><span className="agent-mark" aria-hidden="true">m</span>
          <h3>A clearer picture,<br />one conversation at a time.</h3>
          <p>{anonymous ? "Sign in to begin a conversation with Maipa." : "Conversations will stay here while you work with your books and evidence."}</p>
        </div>}
        <p className="agent-note">Thoughtful preparation. Clear evidence. You stay in control.</p>
      </section>
      <section className="work-panel" data-pane="work" aria-label="Work area" inert={narrow && pane !== "work"}>
        <div className="work-heading"><div><span className="eyebrow">Your workspace</span><h2>Work area</h2></div>
          <div className="session-controls"><span className="connection" role="status"><span aria-hidden="true" />{status}</span>
            {session.state === 'authenticated' && <Button variant="ghost" onClick={() => setPane('agent')}>Account setup</Button>}
            {session.state === "authenticated" && <SignOutButton refresh={refresh} />}</div>
        </div>
        <div className="work-content">
          {session.state === 'authenticated' && <div className="layout-status" data-preferences-state={preferences.phase}>
            <span role="status">{preferences.phase === 'loading' ? 'Loading your appearance and view…'
              : preferences.phase === 'saving' ? 'Saving appearance and view…' : preferences.phase === 'ready' ? 'Appearance and view saved.'
              : preferences.phase === 'conflict' ? 'Your saved preferences changed. Reload them before making another change.'
              : 'Your preferences could not be confirmed. Reload them before making another change.'}</span>
            {(preferences.phase === 'conflict' || preferences.phase === 'unavailable') && <Button variant="ghost"
              onClick={() => { void preferencesController.current?.load(); }}>Reload saved preferences</Button>}
          </div>}
          {session.state === 'authenticated' && <div className="layout-status" data-layout-state={layout.phase}>
            <span role="status">{layout.phase === 'loading' ? 'Loading your pane width…' : layout.phase === 'saving' ? 'Saving pane width…'
              : layout.phase === 'ready' ? 'Pane width ready.' : layout.phase === 'conflict' ? 'Your saved layout changed. Reload it before resizing.'
              : 'Pane width could not be confirmed. Reload the saved layout before resizing.'}</span>
            {(layout.phase === 'conflict' || layout.phase === 'unavailable') && <Button variant="ghost" onClick={() => { void layoutController.current?.load(); }}>Reload saved layout</Button>}
          </div>}
          {session.state === "authenticated" ? <DynamicUserPanel key={session.userId} userId={session.userId} factsAttempt={factsAttempt}
            preferences={preferences} onSelectBuiltin={active_view => savePreferences({ active_view })} /> : <Card className="welcome-card" variant="outlined" sections="plain">
            <Card.Body><span className="eyebrow">A calm place to work</span>
              {!anonymous && <><h3>{failed ? "Workspace unavailable" : "Opening your workspace"}</h3>
              <p>{failed ? "We could not check your session. Try again to reconnect."
                : "Books, evidence and reviewed proposals share this work area, alongside your conversation with Maipa."}</p></>}
              {failed && <Button className="retry-button" onClick={refresh}>Try again</Button>}
              {anonymous && <LocalAccess refresh={refresh} />}
              <div className="workspace-principles"><div><span aria-hidden="true">01</span><strong>Keep the source</strong><p>Financial work stays connected to its evidence.</p></div>
                <div><span aria-hidden="true">02</span><strong>Review the proposal</strong><p>Know what is being changed before you approve it.</p></div>
                <div><span aria-hidden="true">03</span><strong>Verify the outcome</strong><p>Clear results, with a record you can follow.</p></div></div>
            </Card.Body>
          </Card>}
          {session.state !== "authenticated" && <p className="privacy-note">No financial records are shown until your workspace access is established.</p>}
        </div>
        <footer className="work-footer"><span>Maipa</span><BuildUpdates key={updatesStorageKey(session)} session={session} /></footer>
      </section>
    </ResizablePanels>
  </div>;
}

export default function App() {
  const [session, setSession] = useState<SessionState>({ state: "checking" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    setSession({ state: "checking" });
    void readSession().then(value => { if (current) setSession(value); });
    return () => { current = false; };
  }, [attempt]);
  return <Workspace session={session} refresh={() => setAttempt(value => value + 1)} />;
}
