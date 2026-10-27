import { useEffect, useRef, useState } from 'react';
import { Button, ChatDrawer, ContentRenderer, Field, Input, PromptInput, ResizablePanels, Select, Text, VirtualList, renderMarkdown,
  type PromptAttachment, type PromptDraft, type SubmitResult } from '@particle-academy/react-fancy';
import { AccountSetupChat } from './AccountSetupChat.js';
import { AgentSurface } from './AgentSurface.js';
import { chatRequest, emptyChat, newerChat, readEventStream, reconnectChat, type ChatState } from './agent-chat.js';
import { SharedUiBridge } from './shared-ui-bridge.js';
import type { LayoutState } from './layout.js';
import { readOnboarding } from './onboarding.js';
import { CHAT_PROSE } from './content-policy.js';
import { ChatDrafts, emptyDraft, sendProblem } from './chat-drafts.js';

const phaseLabels = { offline: 'Connect your agent to begin', connecting: 'Connecting…', sign_in: 'Sign in to your provider',
  ready: 'Ready', thinking: 'Maipa is working…', stopping: 'Stopping…', unavailable: 'Connection needs attention' };

export function AgentWorkspace({ userId, layout, onResize, onResizeCommit }: { userId: string; layout: LayoutState;
  onResize: (value: number) => void; onResizeCommit: (value: number) => void }) {
  const [state, setState] = useState<ChatState>(emptyChat);
  const [, refreshDraft] = useState(0);
  const drafts = useRef(new ChatDrafts());
  const draftAccount = state.connection.account_id ?? 'unselected';
  const draft = drafts.current.read(draftAccount);
  function changeDraft(value: PromptDraft) { drafts.current.write(draftAccount, value); refreshDraft(value => value + 1); }
  function setDraft(text: string) { changeDraft({ ...emptyDraft(), text }); }
  const [sendError, setSendError] = useState('');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerTab, setDrawerTab] = useState('requests');
  const [follow, setFollow] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const [authorizationCode, setAuthorizationCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [focusPanel, setFocusPanel] = useState(false);
  const [narrow, setNarrow] = useState(false);
  const [setup, setSetup] = useState(false);
  const current = useRef(state); current.current = state;
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setAuthorizationCode(''); }, [userId, state.connection.account_id, state.phase, state.login?.url]);
  useEffect(() => { setFollow(true); setUnseen(0); setSendError(''); }, [draftAccount]);
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
  const ui = state.ui?.composition ? state.ui : null;
  useEffect(() => { if (!ui) setFocusPanel(false); }, [!!ui]);
  async function action(payload: Record<string, unknown>) {
    if (busy) return;
    setBusy(true); setError('');
    try {
      const next = await chatRequest(userId, { ...payload, selection_revision: current.current.connection.revision });
      // A newer stream checkpoint can arrive before the POST acknowledgement.
      setState(previous => newerChat(previous, next));
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
  async function submit(text: string, attachments: PromptAttachment[]): Promise<SubmitResult> {
    const problem = sendProblem(text, attachments);
    if (problem) return { ok: false, error: problem };
    const selected = current.current.connection;
    if (busy || error || !selected.available || !selected.account_id || !['offline', 'unavailable', 'ready'].includes(current.current.phase))
      return { ok: false, error: 'Connect Maipa and wait for the current reply before sending.' };
    const account = selected.account_id;
    const request_id = drafts.current.request(account, text);
    setBusy(true); setSendError(''); setFollow(true);
    try {
      const next = await chatRequest(userId, { action: 'send', revision: current.current.revision,
        selection_revision: selected.revision, request_id, text });
      if (!alive.current || current.current.connection.account_id !== account || current.current.connection.revision !== selected.revision)
        return { ok: false, error: 'The connection changed. Your draft has been kept.' };
      setState(previous => newerChat(previous, next));
      drafts.current.acknowledge(account, request_id);
      // Fancy clears only its acknowledged matching draft; newer typing survives.
    } catch {
      return { ok: false, error: 'Maipa could not confirm the send. Your draft has been kept; try again.' };
    } finally { if (alive.current) setBusy(false); }
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
          {!!state.messages.length && <VirtualList className="chat-transcript" height="100%" items={state.messages}
            followTail={follow} onFollowChange={setFollow} onUnseenChange={setUnseen} estimateRowHeight={100} overscan={8}
            role="log" aria-label="Conversation with Maipa" aria-live="polite" aria-relevant="additions text"
            renderRow={message => <article className={`chat-message ${message.role}`} data-message-id={message.id}>
              <span>{message.role === 'user' ? 'You' : 'Maipa'}</span>
              {message.role === 'user' ? <Text size="sm" className="chat-user-text">{message.text}</Text>
                : <ContentRenderer value={renderMarkdown(message.text, CHAT_PROSE)} format="html" className="maipa-content" />}
              {message.state === 'interrupted' && <small>Interrupted · partial reply retained</small>}
            </article>} />}
          {!follow && <Button size="sm" variant="ghost" onClick={() => setFollow(true)}>Back to latest{unseen ? ` (${unseen} new)` : ''}</Button>}
          {setup && <div className="chat-setup-panel"><AccountSetupChat providerReady={state.phase === 'ready' || state.phase === 'thinking'}
            onOpenFacts={() => { setSetup(false); setDraft('Open my saved client facts'); }} /></div>}
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
          {sendError && <Text size="sm" role="alert" className="auth-message">{sendError}</Text>}
          <div className="chat-composer" aria-label="Message Maipa">
            <Field label="Message Maipa" labelHidden htmlFor="maipa-chat-message">
              <PromptInput inputId="maipa-chat-message" name="message" placeholder="Ask Maipa…" draft={draft} onDraftChange={changeDraft}
                budgetTokens={0} meter="hidden" maxHeight={160} attachButton="icon" sendButton="icon"
                sendDisabled={busy || !!error || !state.connection.available || running}
                onSubmit={submit} onSubmitError={problem => setSendError(typeof problem === 'string' ? problem : 'The message was not sent. Your draft has been kept.')}
                aboveInput={<ChatDrawer tabs={[{ id: 'requests', label: 'Requests', number: null }, { id: 'connection', label: 'Connection', number: null }]}
                  className="maipa-chat-drawer" activeTabId={drawerTab} onTabChange={tab => { setDrawerTab(tab); setDrawerOpen(true); }} open={drawerOpen} onToggle={setDrawerOpen} minBodyHeight={52}>
                  {drawerTab === 'requests' ? <div className="chat-quick-requests">
                    <Button size="sm" variant="ghost" onClick={() => setDraft('Open my saved client facts')}>Review client facts</Button>
                    <Button size="sm" variant="ghost" onClick={() => setDraft('Present a note')}>Ask for a note</Button>
                  </div> : <Text size="sm">{providerLabel} · {phaseLabels[state.phase]}{state.models?.currentModelId ? ` · ${state.models.availableModels.find(model => model.modelId === state.models!.currentModelId)?.name ?? state.models.currentModelId}` : ''}</Text>}
                </ChatDrawer>} />
            </Field>
            {running && <Button type="button" disabled={busy || state.phase === 'stopping'}
              onClick={() => void action({ action: state.phase === 'thinking' ? 'cancel' : 'disconnect' })}>Stop</Button>}
          </div>
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
