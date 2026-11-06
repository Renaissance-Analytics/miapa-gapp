import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { Button, Callout, DatePicker, Input, Select, Stepper, Text } from '@particle-academy/react-fancy';
import { answerOnboarding, readOnboarding, type OnboardingState, type SetupStep } from './onboarding.js';

const labels: Record<string, string> = { display_name: 'Your name', engagement_name: 'Engagement', entity_name: 'Entity',
  currency: 'Currency', reporting_basis: 'Reporting basis', effective_from: 'Applies from', workspace: 'Client setup' };

function shown(key: string, item: string | number | null) {
  return item === null ? 'Unknown for now' : key === 'workspace' ? item === 'new' ? 'New client' : item === 'later' ? 'Do this later' : 'Existing client' : String(item);
}

/** The value already recorded for a step, if any. */
function recorded(value: OnboardingState, step: SetupStep): string | null {
  if (step === 'workspace' && value.draft.workspace === 'existing') return `${value.draft.engagement_name} / ${value.draft.entity_name}`;
  if (step === 'workspace' && value.draft.workspace === undefined) return null;
  if (step in labels && step in value.draft) return shown(step, value.draft[step]);
  return null;
}

/** Every remaining step with its purpose; finished steps show the answer already recorded. */
export function setupStepItems(value: OnboardingState) {
  return value.steps.filter(item => item.id !== 'complete').map(item => ({ id: item.id, title: item.title,
    description: (item.id !== value.step && recorded(value, item.id)) || item.explanation }));
}

export function SetupReview({ value }: { value: OnboardingState }) {
  return <dl className="setup-review" aria-label="Account setup review">{Object.entries(value.draft)
    .filter(([key]) => key in labels).map(([key, item]) => <div key={key}><dt>{labels[key]}</dt>
      <dd>{shown(key, item)}</dd></div>)}</dl>;
}

/** Account setup as explained steps. The connected agent guides it in the conversation; the person can also answer here. */
export function AccountSetupChat({ onOpenFacts, onAskAgent, providerReady = false, syncKey = 0 }: { onOpenFacts: () => void;
  onAskAgent?: () => void; providerReady?: boolean; syncKey?: number }) {
  const [value, setValue] = useState<OnboardingState | null>(null);
  const [answer, setAnswer] = useState('');
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState('');
  const [reload, setReload] = useState(0);
  const [conflict, setConflict] = useState(false);
  const shownRevision = useRef<number | null>(null);
  const prepare = (next: OnboardingState) => {
    // Keep the person's typing unless the step itself moved (for example, the agent recorded an answer).
    if (shownRevision.current !== next.revision) setAnswer(next.step === 'effective_from' ? new Date().toISOString().slice(0, 10) : '');
    shownRevision.current = next.revision;
    setValue(next);
  };
  useEffect(() => {
    let active = true;
    setBusy(true);
    void readOnboarding().then(result => {
      if (!active) return;
      setBusy(false);
      if (result.state === 'ready') { prepare(result.value); setConflict(false); setMessage(''); }
      else setMessage('Account setup could not be loaded. Try again.');
    });
    return () => { active = false; };
  }, [reload, syncKey]);

  async function send(reply: string, action: 'reply' | 'reset' | 'resume' = 'reply') {
    if (!value || busy || conflict) return;
    setBusy(true); setMessage('');
    const result = await answerOnboarding(value.setup_id, value.revision, reply, action);
    setBusy(false);
    if (result.state === 'ready') { prepare(result.value); return; }
    if (result.state === 'conflict') { setConflict(true); setMessage('This setup changed, possibly from your conversation with Maipa. Reload it to see the latest answers.'); }
    else setMessage(result.state === 'invalid' ? 'That answer does not fit this step. Read the explanation above and try again.' : result.state === 'denied'
      ? 'Your access does not allow this setup change. Check your session and role.' : 'We could not save that answer. Reload before retrying.');
    // Unknown transport outcomes must be reconciled before another write.
    if (result.state === 'unavailable') setConflict(true);
  }
  function submit(event: FormEvent) { event.preventDefault(); void send(answer); }

  if (!value) return <div className="setup-chat" aria-label="Account onboarding" aria-busy={busy}>
    {busy ? <p role="status">Opening your saved setup…</p> : <><p className="auth-message" role="alert">{message}</p>
      <Button onClick={() => setReload(value => value + 1)}>Reload setup</Button></>}
  </div>;

  const step = value.step;
  const index = step === 'complete' ? value.steps.length : value.steps.findIndex(item => item.id === step);
  const visible = value.steps.filter(item => item.id !== 'complete');
  const position = Math.min(index + 1, visible.length);
  const editing = step !== 'review' && step !== 'complete';
  const form: ReactNode = editing && <form onSubmit={submit} aria-label="Answer this setup step yourself">
    {value.choices.length ? <Select id="maipa-setup-answer" label={value.question} value={answer} onValueChange={setAnswer}
      list={value.choices} placeholder="Choose an answer" disabled={busy || conflict} />
      : step === 'effective_from' ? <DatePicker id="maipa-setup-answer" label={value.question} value={answer} onValueChange={setAnswer} required disabled={busy || conflict} />
      : <Input id="maipa-setup-answer" label={value.question} value={answer} onValueChange={setAnswer} autoComplete="off" required
        maxLength={step === 'currency' ? 3 : 128} disabled={busy || conflict} />}
    <Button type="submit" size="sm" disabled={busy || conflict || !answer}>{busy ? 'Saving…' : 'Save answer'}</Button>
  </form>;

  return <div className="setup-chat" aria-label="Account onboarding" aria-busy={busy} data-setup-step={step}>
    <div className="setup-heading">
      <Text size="sm" className="setup-guide-label">{step === 'complete' ? 'Account setup · Done'
        : `Account setup · Step ${position} of ${visible.length}: ${value.steps[index]?.title}`}</Text>
      {step !== 'complete' && <Text size="sm">Each step below says what it is for. Nothing reaches client records until you confirm the final review.</Text>}
    </div>
    {step !== 'complete' && <Callout color="blue" className="setup-how">
      {providerReady
        ? <>Maipa, your connected AI agent, can walk you through these steps in the conversation and record your answers here. You confirm the final save yourself.
          {onAskAgent && <> <Button size="sm" variant="ghost" onClick={onAskAgent}>Ask Maipa to guide me</Button></>}</>
        : <>No AI agent is connected yet, so this panel is a plain form. Connect an agent provider below and Maipa will guide you through these steps in the conversation, or answer each step here yourself.</>}
    </Callout>}
    <Stepper className="setup-steps" aria-label="Account setup steps" current={index} showContent="current"
      steps={setupStepItems(value).map(item => ({ ...item,
        content: item.id === step ? <div className="setup-step-body">
          {item.id === 'review' ? <><SetupReview value={value} />
            <Button disabled={busy || conflict} onClick={() => void send('confirm')}>Confirm and save setup</Button></> : form}
        </div> : undefined }))} />
    {step === 'complete' && <div className="setup-provider-note">
      <Text size="sm">{value.deferred ? 'Client setup is skipped for now. You can continue it here, or ask Maipa, whenever you are ready.'
        : value.entity_id ? 'Your confirmed client facts are saved.' : 'Your profile is saved. Your role allows read-only client access.'}
        {!providerReady && ' Connect an agent provider below to work with Maipa.'}</Text>
      {value.deferred && value.can_edit && <Button size="sm" disabled={busy || conflict} onClick={() => void send('', 'resume')}>Continue client setup</Button>}
      {value.entity_id && <Button size="sm" onClick={onOpenFacts}>Open saved client facts</Button>}
    </div>}
    {message && <p className="auth-message" role="alert">{message}</p>}
    {(message || conflict) && <Button size="sm" disabled={busy} onClick={() => setReload(value => value + 1)}>Reload setup</Button>}
    {step !== 'complete' && <Button size="sm" variant="ghost" disabled={busy || conflict} onClick={() => void send('', 'reset')}>Restart setup draft</Button>}
  </div>;
}
