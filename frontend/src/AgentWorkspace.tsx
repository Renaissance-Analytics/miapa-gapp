import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Button, ResizablePanels } from '@particle-academy/react-fancy';
import { AccountSetupChat } from './AccountSetupChat.js';
import { AgentSurface } from './AgentSurface.js';
import { chatRequest, emptyChat, readEventStream, reconnectChat, type ChatState } from './agent-chat.js';
import { SharedUiBridge } from './shared-ui-bridge.js';
import type { LayoutState } from './layout.js';
import { readOnboarding } from './onboarding.js';

const phaseLabels = { offline: 'Connect your subscription to begin', connecting: 'Connecting…', sign_in: 'Sign in to ChatGPT',
  ready: 'Ready', thinking: 'Maipa is working…', stopping: 'Stopping…', unavailable: 'Connection needs attention' };

export function AgentWorkspace({ userId, layout, onResize, onResizeCommit }: { userId: string; layout: LayoutState;
  onResize: (value: number) => void; onResizeCommit: (value: number) => void }) {
  const [state, setState] = useState<ChatState>(emptyChat);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [focusPanel, setFocusPanel] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [setup, setSetup] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const current = useRef(state); current.current = state;
  const pendingRequest = useRef<{ text: string; id: string } | null>(null);
  const bridge = useRef<SharedUiBridge | null>(null);
  useEffect(() => {
    const host = new SharedUiBridge(userId, () => current.current.ui);
    bridge.current = host;
    return () => { host.dispose(); bridge.current = null; };
  }, [userId]);
  useEffect(() => {
    let active = true;
    void readOnboarding().then(result => { if (active && result.state === 'ready' && result.value.step !== 'complete') setSetup(true); });
    return () => { active = false; };
  }, [userId]);
  useEffect(() => {
    const query = matchMedia('(max-width: 700px)');
    const update = () => setNarrow(query.matches); update();
    query.addEventListener('change', update); return () => query.removeEventListener('change', update);
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    let active = true;
    async function listen() {
      try {
        const initial = await chatRequest(userId, undefined, abort.signal);
        if (!active) return;
        setState(initial); setError('');
        while (active) {
          const response = await fetch('/api/agent/chat/stream/', { credentials: 'same-origin', cache: 'no-store', redirect: 'error',
            headers: { 'X-Maipa-Agent-Actor': userId }, signal: abort.signal });
          if (response.status !== 200 || !response.body) throw Error('Conversation unavailable');
          await readEventStream(response.body, next => { if (active) setState(next); });
        }
      } catch { if (active) setError('The conversation disconnected. Reconnect to load its saved state.'); }
    }
    void listen();
    return () => { active = false; abort.abort(); };
  }, [userId, attempt]);
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [state.messages.at(-1)?.text]);
  const ui = state.ui?.composition ? state.ui : null;
  useEffect(() => { if (!ui) setFocusPanel(false); }, [!!ui]);
  async function action(payload: Record<string, unknown>) {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const next = await chatRequest(userId, payload);
      // A newer stream checkpoint can arrive before the POST acknowledgement.
      setState(previous => next.revision >= previous.revision ? next : previous);
      if (payload.action === 'send') {
        setDraft(previous => previous === payload.text ? '' : previous);
        if (pendingRequest.current?.id === payload.request_id) pendingRequest.current = null;
      }
    } catch (e) { setError(e instanceof Error ? e.message : 'Conversation unavailable'); }
    finally { setBusy(false); }
  }
  async function reconnect() {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const next = await reconnectChat(userId);
      setState(previous => next.revision >= previous.revision ? next : previous);
      setAttempt(value => value + 1);
    } catch (e) { setError(e instanceof Error ? e.message : 'Conversation unavailable'); }
    finally { setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!draft.trim()) return;
    if (pendingRequest.current?.text !== draft) pendingRequest.current = { text: draft, id: crypto.randomUUID() };
    void action({ action: 'send', revision: current.current.revision, request_id: pendingRequest.current.id, text: draft });
  }
  const running = ['connecting', 'sign_in', 'thinking', 'stopping'].includes(state.phase);
  return <main className={`agent-workspace ${ui ? 'has-composition' : 'chat-only'} ${focusPanel ? 'focus-panel' : 'focus-chat'}`}>
    {ui && <div className="context-switch" role="group" aria-label="Visible panel">
      <Button size="sm" variant="ghost" aria-pressed={!focusPanel} onClick={() => setFocusPanel(false)}>Conversation</Button>
      <Button size="sm" variant="ghost" aria-pressed={focusPanel} onClick={() => setFocusPanel(true)}>{ui.composition!.title}</Button>
    </div>}
    <ResizablePanels className="agent-shell-panels" value={layout.split} onValueChange={onResize}
      onValueCommit={onResizeCommit} min={22} max={72} disabled={!ui || layout.phase !== 'ready'} label="Resize conversation and contextual panel">
      <section className="conversation" data-pane="agent" aria-label="Conversation" inert={narrow && !!ui && focusPanel}>
        <div className="conversation-body">
          {!state.messages.length && <div className="conversation-welcome"><span className="eyebrow">Your AI accountant</span>
            <h2>What would you like to work on?</h2><p>Ask Maipa. The tools and records you need will open alongside our conversation.</p></div>}
          <div className="chat-transcript" role="log" aria-label="Conversation with Maipa" aria-live="polite" aria-relevant="additions text">
            {state.messages.map(message => <article className={`chat-message ${message.role}`} key={message.id} data-message-id={message.id}>
              <span>{message.role === 'user' ? 'You' : 'Maipa'}</span><p>{message.text}</p>
              {message.state === 'interrupted' && <small>Interrupted · partial reply retained</small>}
            </article>)}
          </div>
          {setup && <AccountSetupChat providerReady={state.phase === 'ready' || state.phase === 'thinking'}
            onOpenFacts={() => { setSetup(false); setDraft('Open my saved client facts'); }} />}
          {state.phase === 'sign_in' && <div className="provider-signin" role="region" aria-label="Connect ChatGPT">
            <h3>Connect your ChatGPT subscription</h3><p>This login belongs to your Maipa account.</p>
            {state.login ? <><a href={state.login.url} target="_blank" rel="noreferrer">Continue with ChatGPT</a>
              <p>Enter <strong className="device-code">{state.login.code}</strong> in the provider’s sign-in window.</p></>
              : <Button disabled={busy} onClick={() => void action({ action: 'login' })}>Sign in with ChatGPT</Button>}
          </div>}
          <div ref={end} />
        </div>
        <div className="chat-controls">
          {error && <p role="alert" className="auth-message">{error} <Button size="sm" variant="ghost" disabled={busy} onClick={() => void reconnect()}>Reconnect</Button></p>}
          <div className="chat-status"><span role="status">{phaseLabels[state.phase]}</span>
            <Button size="sm" variant="ghost" aria-expanded={setup} onClick={() => setSetup(value => !value)}>Account setup</Button>
            {['offline', 'unavailable'].includes(state.phase) && <Button size="sm" variant="ghost" disabled={busy}
              onClick={() => void reconnect()}>Connect ChatGPT</Button>}
            {state.phase === 'ready' && <Button size="sm" variant="ghost" disabled={busy} onClick={() => void action({ action: 'disconnect' })}>Disconnect</Button>}
          </div>
          <form className="chat-composer" onSubmit={submit} aria-label="Message Maipa">
            <textarea aria-label="Message Maipa" placeholder="Ask Maipa…" value={draft} maxLength={8000} rows={2}
              onChange={event => setDraft(event.target.value)} onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (!running && !busy && !error) submit(event); }
              }} />
            {running ? <Button type="button" disabled={busy || state.phase === 'stopping'}
              onClick={() => void action({ action: state.phase === 'thinking' ? 'cancel' : 'disconnect' })}>Stop</Button>
              : <Button type="submit" disabled={busy || !!error || !draft.trim()}>Send</Button>}
          </form>
        </div>
      </section>
      <section className="contextual-panel" data-pane="work" aria-label="Contextual panel" hidden={!ui} inert={!ui || (narrow && !focusPanel)}>
        {ui && <><header><h2>{ui.composition!.title}</h2><Button size="sm" variant="ghost" aria-label="Close contextual panel" disabled={busy}
          onClick={() => { if (!bridge.current) return; setBusy(true); void bridge.current.dismiss(ui).catch(() => setError('The shared panel changed. Reconnect before closing it.')).finally(() => setBusy(false)); }}>Close</Button></header>
          <div className="composition-body"><AgentSurface userId={userId} ui={ui} /></div></>}
      </section>
    </ResizablePanels>
  </main>;
}
