import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Button, Input, ResizablePanels, Select } from '@particle-academy/react-fancy';
import { AccountSetupChat } from './AccountSetupChat.js';
import { AgentSurface } from './AgentSurface.js';
import { chatRequest, emptyChat, newerChat, readEventStream, reconnectChat, type ChatState } from './agent-chat.js';
import { SharedUiBridge } from './shared-ui-bridge.js';
import type { LayoutState } from './layout.js';
import { readOnboarding } from './onboarding.js';

const phaseLabels = { offline: 'Connect your agent to begin', connecting: 'Connecting…', sign_in: 'Sign in to your provider',
  ready: 'Ready', thinking: 'Maipa is working…', stopping: 'Stopping…', unavailable: 'Connection needs attention' };

export function AgentWorkspace({ userId, layout, onResize, onResizeCommit }: { userId: string; layout: LayoutState;
  onResize: (value: number) => void; onResizeCommit: (value: number) => void }) {
  const [state, setState] = useState<ChatState>(emptyChat);
  const [draft, setDraft] = useState('');
  const [authorizationCode, setAuthorizationCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [focusPanel, setFocusPanel] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [setup, setSetup] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const current = useRef(state); current.current = state;
  useEffect(() => { setAuthorizationCode(''); }, [userId, state.connection.account_id, state.phase, state.login?.url]);
  const pendingRequest = useRef<{ text: string; id: string } | null>(null);
  const drafts = useRef(new Map<string, { text: string; pending: typeof pendingRequest.current }>());
  const draftAccount = useRef('unselected');
  const draftValue = useRef(draft); draftValue.current = draft;
  useEffect(() => {
    const next = state.connection.account_id ?? 'unselected';
    if (next === draftAccount.current) return;
    drafts.current.set(draftAccount.current, { text: draftValue.current, pending: pendingRequest.current });
    const saved = drafts.current.get(next);
    draftAccount.current = next; pendingRequest.current = saved?.pending ?? null;
    setDraft(saved?.text ?? '');
  }, [state.connection.account_id]);
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
        setState(previous => newerChat(previous, initial)); setError('');
        while (active) {
          const response = await fetch('/api/agent/chat/stream/', { credentials: 'same-origin', cache: 'no-store', redirect: 'error',
            headers: { 'X-Maipa-Agent-Actor': userId, 'X-Maipa-Agent-Models': '1' }, signal: abort.signal });
          if (response.status !== 200 || !response.body) throw Error('Conversation unavailable');
          await readEventStream(response.body, next => { if (active) setState(previous => newerChat(previous, next)); });
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
      const next = await chatRequest(userId, { ...payload, selection_revision: current.current.connection.revision });
      // A newer stream checkpoint can arrive before the POST acknowledgement.
      setState(previous => newerChat(previous, next));
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
      setState(previous => newerChat(previous, next));
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
  const canSwitch = !busy && !running && ['offline', 'unavailable'].includes(state.phase);
  const providerLabel = state.connection.label ?? 'your provider';
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
          {state.phase === 'sign_in' && <div className="provider-signin" role="region" aria-label={`Connect ${providerLabel}`}>
            <h3>Connect {providerLabel}</h3><p>This login belongs to your Maipa account.</p>
            {state.login ? <><a href={state.login.url} target="_blank" rel="noreferrer">Continue with {providerLabel}</a>
              {state.login.code && <p>Enter <strong className="device-code">{state.login.code}</strong> in the provider’s sign-in window.</p>}
              {state.login.accepts_code && <form aria-label="Complete provider sign-in" onSubmit={event => {
                event.preventDefault();
                const code = authorizationCode.trim();
                if (!code || busy) return;
                setAuthorizationCode('');
                void action({ action: 'authorize', code });
              }}>
                <Input id="maipa-provider-authorization" label="If the provider shows a login code, paste it here"
                  type="password" autoComplete="off" maxLength={2048} value={authorizationCode}
                  onValueChange={setAuthorizationCode} disabled={busy} />
                <Button type="submit" size="sm" disabled={busy || !authorizationCode.trim()}>Complete sign-in</Button>
              </form>}</>
              : <Button disabled={busy} onClick={() => void action({ action: 'login' })}>Sign in with {providerLabel}</Button>}
          </div>}
          <div ref={end} />
        </div>
        <div className="chat-controls">
          {error && <p role="alert" className="auth-message">{error} <Button size="sm" variant="ghost" disabled={busy} onClick={() => void reconnect()}>Reconnect</Button></p>}
          <div className="chat-status"><span role="status">{!state.connection.provider ? 'Choose your agent provider' :
            !state.connection.available ? `Connection support for ${providerLabel} is not available in this build.` : phaseLabels[state.phase]}</span>
            <div className="chat-provider-choice"><Select id="maipa-agent-provider" size="sm" label="Agent provider" labelHidden value={state.connection.provider ?? ''}
              placeholder="Choose provider" disabled={!canSwitch || !!error} list={[
                ...state.providers.map(provider => ({ value: provider.id, label: provider.label })),
                ...(state.connection.provider && !state.providers.some(p => p.id === state.connection.provider)
                  ? [{ value: state.connection.provider, label: providerLabel }] : []),
              ]} onValueChange={provider => void action({ action: 'select_provider', provider })} /></div>
            {state.models && <div className="chat-provider-choice"><Select id="maipa-agent-model" size="sm" label="Agent model" labelHidden
              value={state.models.currentModelId ?? ''} placeholder="Provider default" disabled={busy || state.phase !== 'ready' || !!error}
              list={state.models.availableModels.map(model => ({ value: model.modelId, label: model.name }))}
              onValueChange={model_id => void action({ action: 'set_model', revision: current.current.revision, model_id })} /></div>}
            <Button size="sm" variant="ghost" aria-expanded={setup} onClick={() => setSetup(value => !value)}>Account setup</Button>
            {state.connection.available && ['offline', 'unavailable'].includes(state.phase) && <Button size="sm" variant="ghost" disabled={busy}
              onClick={() => void reconnect()}>Connect {providerLabel}</Button>}
            {state.phase === 'ready' && <Button size="sm" variant="ghost" disabled={busy} onClick={() => void action({ action: 'disconnect' })}>Disconnect</Button>}
          </div>
          <form className="chat-composer" onSubmit={submit} aria-label="Message Maipa">
            <textarea aria-label="Message Maipa" placeholder="Ask Maipa…" value={draft} maxLength={8000} rows={2}
              onChange={event => setDraft(event.target.value)} onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); if (state.connection.available && !running && !busy && !error) submit(event); }
              }} />
            {running ? <Button type="button" disabled={busy || state.phase === 'stopping'}
              onClick={() => void action({ action: state.phase === 'thinking' ? 'cancel' : 'disconnect' })}>Stop</Button>
              : <Button type="submit" disabled={busy || !!error || !state.connection.available || !draft.trim()}>Send</Button>}
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
