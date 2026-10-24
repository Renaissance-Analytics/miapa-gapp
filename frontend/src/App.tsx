import { useEffect, useState } from "react";
import { Button, Card, initTheme, ResizablePanels, useTheme } from "@particle-academy/react-fancy";
import { readSession, themeStorageKey, type SessionState } from "./api.js";
import { BuildUpdates } from "./BuildUpdates.js";
import { updatesStorageKey } from "./updates.js";
import { SignInForm, SignOutButton } from "./Authentication.js";
import { IntakeWorkspace } from "./IntakeWorkspace.js";

export function Workspace({ session, refresh }: { session: SessionState; refresh: () => void }) {
  const theme = useTheme();
  const [split, setSplit] = useState(28);
  const [pane, setPane] = useState<"agent" | "work">("work");
  const [narrow, setNarrow] = useState(false);

  useEffect(() => initTheme({ storageKey: themeStorageKey(session) }), [session]);
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
          aria-pressed={theme.preference === value} onClick={() => theme.setPreference(value)}>
          {value === "system" ? "System" : value === "light" ? "Light" : "Dark"}
        </Button>)}
      </div>
    </header>
    <div className="mobile-panes" role="group" aria-label="Visible panel">
      <Button variant="ghost" aria-pressed={pane === "agent"} onClick={() => setPane("agent")}>Conversation</Button>
      <Button variant="ghost" aria-pressed={pane === "work"} onClick={() => setPane("work")}>Work area</Button>
    </div>
    <ResizablePanels className="workspace-panels" value={split} onValueChange={setSplit} min={22} max={45}
      label="Resize conversation and work area">
      <section className="agent-panel" data-pane="agent" aria-label="Conversation" inert={narrow && pane !== "agent"}>
        <div className="panel-heading"><span className="eyebrow">Your financial copilot</span><h2>Conversation</h2></div>
        <div className="agent-empty"><span className="agent-mark" aria-hidden="true">m</span>
          <h3>A clearer picture,<br />one conversation at a time.</h3>
          <p>{anonymous ? "Sign in to begin a conversation with Maipa." : "Conversations will stay here while you work with your books and evidence."}</p>
        </div>
        <p className="agent-note">Thoughtful preparation. Clear evidence. You stay in control.</p>
      </section>
      <section className="work-panel" data-pane="work" aria-label="Work area" inert={narrow && pane !== "work"}>
        <div className="work-heading"><div><span className="eyebrow">Your workspace</span><h2>Work area</h2></div>
          <div className="session-controls"><span className="connection" role="status"><span aria-hidden="true" />{status}</span>
            {session.state === "authenticated" && <SignOutButton refresh={refresh} />}</div>
        </div>
        <div className="work-content">
          {session.state === "authenticated" ? <IntakeWorkspace key={session.userId} /> : <Card className="welcome-card" variant="outlined" sections="plain">
            <Card.Body><span className="eyebrow">A calm place to work</span>
              <h3>{failed ? "Workspace unavailable" : anonymous ? "Sign in to begin" : session.state === "checking" ? "Opening your workspace" : "Your financial work, together"}</h3>
              <p>{failed ? "We could not check your session. Try again to reconnect." : anonymous
                ? "Your books, documents and conversations belong to your private workspace. Sign in is required before accessing them."
                : "Books, evidence and reviewed proposals share this work area, alongside your conversation with Maipa."}</p>
              {failed && <Button className="retry-button" onClick={refresh}>Try again</Button>}
              {anonymous && <SignInForm refresh={refresh} />}
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
