import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Button, DatePicker, Input, Select } from '@particle-academy/react-fancy';
import { answerOnboarding, readOnboarding, type OnboardingState } from './onboarding.js';

const labels: Record<string, string> = { display_name: 'Your name', engagement_name: 'Engagement', entity_name: 'Entity',
  currency: 'Currency', reporting_basis: 'Reporting basis', effective_from: 'Applies from', workspace: 'Client setup' };

export function SetupReview({ value }: { value: OnboardingState }) {
  return <dl className="setup-review" aria-label="Account setup review">{Object.entries(value.draft)
    .filter(([key]) => key in labels).map(([key, item]) => <div key={key}><dt>{labels[key]}</dt>
      <dd>{item === null ? 'Unknown for now' : key === 'workspace' ? item === 'new' ? 'New client' : item === 'later' ? 'Do this later' : 'Existing client' : String(item)}</dd></div>)}</dl>;
}

export function AccountSetupChat({ onOpenFacts }: { onOpenFacts: () => void }) {
  const [value, setValue] = useState<OnboardingState | null>(null);
  const [answer, setAnswer] = useState('');
  const [busy, setBusy] = useState(true);
  const [message, setMessage] = useState('');
  const [reload, setReload] = useState(0);
  const [conflict, setConflict] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let active = true;
    setBusy(true); setMessage('');
    void readOnboarding().then(result => {
      if (!active) return;
      setBusy(false);
      if (result.state === 'ready') { setValue(result.value); setConflict(false); setAnswer(result.value.step === 'effective_from' ? new Date().toISOString().slice(0, 10) : ''); }
      else setMessage('Account setup could not be loaded. Try again.');
    });
    return () => { active = false; };
  }, [reload]);
  useEffect(() => { end.current?.scrollIntoView({ block: 'nearest' }); }, [value?.revision]);

  async function send(reply: string, action: 'reply' | 'reset' | 'resume' = 'reply') {
    if (!value || busy || conflict) return;
    setBusy(true); setMessage('');
    const result = await answerOnboarding(value.setup_id, value.revision, reply, action);
    setBusy(false);
    if (result.state === 'ready') { setValue(result.value); setAnswer(result.value.step === 'effective_from' ? new Date().toISOString().slice(0, 10) : ''); return; }
    if (result.state === 'conflict') { setConflict(true); setMessage('This setup or its client facts changed. Reload the conversation. If client facts changed, restart the draft and select them again.'); }
    else setMessage(result.state === 'invalid' ? 'Check your answer and try again.' : result.state === 'denied'
      ? 'Your access does not allow this setup change. Check your session and role.' : 'We could not save that answer. Reload the conversation before retrying.');
    // Unknown transport outcomes must be reconciled before another write.
    if (result.state === 'unavailable') setConflict(true);
  }
  function submit(event: FormEvent) { event.preventDefault(); void send(answer); }
  const step = value?.step;
  return <div className="setup-chat" aria-label="Account onboarding" aria-busy={busy} data-setup-step={step}>
    <p className="setup-guide-label">Account setup · Guided conversation</p>
    <div className="setup-transcript" role="log" aria-label="Account setup conversation" aria-live="polite">
      {value?.messages.map((item, index) => <div className={`setup-message ${item.role}`} key={index}>
        <span>{item.role === 'guide' ? 'Maipa setup guide' : 'You'}</span><p>{item.text}</p></div>)}
      {value && step === 'review' && <SetupReview value={value} />}
      {value && step === 'complete' && <p className="setup-provider-note">{value.deferred ? 'Client setup is deferred. You can continue it here when ready.' : value.entity_id ? 'Your confirmed client facts are saved.' : 'Your account profile is saved. Your role allows read-only client access.'}
        {' '}Agent connection still requires setup. This guide does not connect a model account.</p>}
      <div ref={end} />
    </div>
    {busy && !value && <p role="status">Opening your saved setup…</p>}
    {message && <p className="auth-message" role="alert">{message}</p>}
    {(message || conflict) && <Button disabled={busy} onClick={() => setReload(value => value + 1)}>Reload conversation</Button>}
    {value && <div className="setup-composer">
      {step === 'review' ? <Button disabled={busy || conflict} onClick={() => void send('confirm')}>Confirm and save setup</Button>
        : step === 'complete' ? <>{value.deferred && value.can_edit && <Button disabled={busy || conflict} onClick={() => void send('', 'resume')}>Continue client setup</Button>}
          {value.entity_id && <Button onClick={onOpenFacts}>Open saved client facts</Button>}</>
        : <form onSubmit={submit} aria-label="Reply to account setup">
          {value.choices.length ? <Select id="maipa-setup-answer" label="Your answer" value={answer} onValueChange={setAnswer}
            list={value.choices} placeholder="Choose an answer" disabled={busy || conflict} />
            : step === 'effective_from' ? <DatePicker id="maipa-setup-answer" label="Effective date" value={answer} onValueChange={setAnswer} required disabled={busy || conflict} />
            : <Input id="maipa-setup-answer" label={step === 'currency' ? 'Currency code' : 'Your answer'}
              value={answer} onValueChange={setAnswer} autoComplete="off" required
              maxLength={step === 'currency' ? 3 : 128} disabled={busy || conflict} />}
          <Button type="submit" disabled={busy || conflict || !answer}>{busy ? 'Saving…' : 'Send answer'}</Button>
        </form>}
      {step !== 'complete' && <Button variant="ghost" disabled={busy || conflict} onClick={() => void send('', 'reset')}>Restart setup draft</Button>}
    </div>}
  </div>;
}
