import { useEffect, useState, type FormEvent } from 'react';
import { Button, Card, DatePicker, Input, Select } from '@particle-academy/react-fancy';
import { addEntity, CHOICES, createEngagement, FACT_KEYS, FACT_LABELS, formatFact, label, listEngagements,
  readEntity, readHistory, saveFact, type Engagement, type EntityProfile, type FactKey, type FactRevision,
  type FactValue, type Failure, type History } from './intake.js';

const today = () => new Date().toISOString().slice(0, 10);
const messages: Record<Failure, string> = {
  invalid: 'Check the entered value, evidence and dates. Nothing was saved.',
  signed_out: 'Your session ended. Sign in again to continue.', denied: 'Your current workspace access does not allow this action.',
  conflict: 'This record changed. Reload the current record and review your changes before saving again.',
  unavailable: 'We could not confirm the result. Reload the current record before trying again.',
};
const rowFields: Partial<Record<FactKey, { key: string; label: string; type?: string }[]>> = {
  ownership: [{ key: 'name', label: 'Owner name' }, { key: 'percentage', label: 'Ownership percentage' }],
  periods: [{ key: 'name', label: 'Period name' }, { key: 'start', label: 'Period start', type: 'date' }, { key: 'end', label: 'Period end', type: 'date' }],
  tax_elections: [{ key: 'name', label: 'Election name' }, { key: 'confirmed', label: 'Election confirmed', type: 'boolean' }],
  authorized_contacts: [{ key: 'name', label: 'Contact name' }, { key: 'role', label: 'Contact role' }, { key: 'email', label: 'Contact email', type: 'email' }],
};
const listKey = (key: FactKey) => !!rowFields[key] || key === 'jurisdictions' || key === 'systems';

function ValueEditor({ factKey, value, change, disabled }: { factKey: FactKey; value: FactValue | null;
  change: (value: FactValue) => void; disabled: boolean }) {
  const choices = CHOICES[factKey];
  if (choices) return <Select id="intake-recorded-value" label="Recorded value" list={choices.map(v => ({ value: v, label: label(v) }))}
    placeholder="Select the recorded value" required value={typeof value === 'string' ? value : ''} onValueChange={change} disabled={disabled} />;
  if (factKey === 'records_complete') return <Select id="intake-recorded-value" label="Records complete" list={[{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }]}
    placeholder="Choose yes or no" required value={typeof value === 'boolean' ? String(value) : ''} onValueChange={v => change(v === 'true')} disabled={disabled} />;
  if (listKey(factKey)) {
    const fields = rowFields[factKey] ?? [{ key: 'value', label: factKey === 'systems' ? 'Source system' : 'Jurisdiction code' }];
    const rows = Array.isArray(value) ? value : [];
    const replace = (index: number, field: string, next: string | boolean) => change(rows.map((row, n) => n === index
      ? rowFields[factKey] ? { ...(typeof row === 'object' ? row : {}), [field]: next } : String(next) : row));
    return <div className="intake-rows"><p>{rows.length ? 'Record each item separately.' : 'No items means none recorded. Choose Unknown if you do not know.'}</p>
      {factKey === 'ownership' && <p>Use decimal percentages. Partial ownership lists may total less than 100.</p>}
      {rows.map((row, index) => <fieldset key={index} disabled={disabled}><legend>{label(factKey)} {index + 1}</legend>
        {fields.map(field => {
          const item = typeof row === 'string' ? row : row[field.key] ?? '';
          return field.type === 'boolean' ? <Select key={field.key} label={`${field.label} ${index + 1}`} required
            list={[{ value: 'true', label: 'Yes' }, { value: 'false', label: 'No' }]} placeholder="Choose yes or no"
            value={typeof item === 'boolean' ? String(item) : ''} onValueChange={v => replace(index, field.key, v === 'true')} />
            : field.type === 'date' ? <DatePicker key={field.key} label={`${field.label} ${index + 1}`} required
              value={String(item)} onValueChange={v => replace(index, field.key, v)} />
            : <Input key={field.key} label={`${field.label} ${index + 1}`} type={field.type === 'email' ? 'email' : 'text'} required
              maxLength={field.type === 'email' ? 254 : 128} value={String(item)} onValueChange={v => replace(index, field.key, v)} />;
        })}
        <Button type="button" variant="ghost" onClick={() => change(rows.filter((_, n) => n !== index))}>Remove item {index + 1}</Button>
      </fieldset>)}
      <Button type="button" variant="ghost" disabled={disabled || rows.length >= 50} onClick={() => change([...rows,
        rowFields[factKey] ? Object.fromEntries(fields.map(f => [f.key, ''])) : ''])}>Add item</Button>
    </div>;
  }
  return <Input id="intake-recorded-value" label="Recorded value" required maxLength={factKey === 'engagement_scope' ? 1024 : 128}
    description={factKey === 'currency' ? 'Three uppercase letters, such as USD.' : undefined}
    value={typeof value === 'string' ? value : ''} onValueChange={change} disabled={disabled} />;
}

function FactEditor({ profile, factKey, saved, reload }: { profile: EntityProfile; factKey: FactKey;
  saved: () => void; reload: () => void }) {
  const current = profile.facts[factKey];
  const existing = 'evidence' in current ? current : undefined;
  const [knowledge, setKnowledge] = useState<'known' | 'unknown'>(current.knowledge);
  const [value, setValue] = useState<FactValue | null>(current.value);
  const [start, setStart] = useState(existing?.effective_from ?? today());
  const [end, setEnd] = useState(existing?.effective_to ?? '');
  const [reference, setReference] = useState(existing?.evidence.reference ?? '');
  const [locator, setLocator] = useState(existing?.evidence.locator ?? '');
  const [observed, setObserved] = useState(existing?.evidence.observed_on ?? today());
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<Failure>();
  const blocked = pending || !profile.can_edit || failure === 'conflict' || failure === 'unavailable' || failure === 'signed_out' || failure === 'denied';
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (blocked) return;
    setPending(true); setFailure(undefined);
    const result = await saveFact(profile.id, { key: factKey, knowledge, value, expected_revision: profile.revision,
      effective_from: start, effective_to: end || null, evidence: { reference, locator, observed_on: observed } });
    setPending(false);
    if (result.kind === 'ok') saved(); else setFailure(result.kind);
  }
  return <form className="intake-form" aria-label="Record client fact" onSubmit={submit} autoComplete="off">
    <h4>Record {FACT_LABELS[factKey].toLowerCase()}</h4>
    {!profile.can_edit && <p>Your current role can read this record.</p>}
    <Select id="intake-knowledge" label="What is known?" value={knowledge} disabled={blocked}
      list={[{ value: 'unknown', label: 'Unknown' }, { value: 'known', label: 'Known from evidence' }]}
      onValueChange={next => { setKnowledge(next as 'known' | 'unknown'); if (next === 'unknown') setValue(null);
        else if (value === null) setValue(listKey(factKey) ? [] : null); }} />
    {knowledge === 'known' && <ValueEditor factKey={factKey} value={value} change={setValue} disabled={blocked} />}
    {factKey === 'legal_form' && <p>Legal form does not determine tax classification. Record tax facts separately.</p>}
    {factKey === 'authorized_contacts' && <p>Recording a contact does not grant workspace access.</p>}
    <div className="intake-date-pair"><DatePicker id="intake-effective-from" label="Effective from" required value={start} onValueChange={setStart} disabled={blocked} />
      <DatePicker id="intake-effective-to" label="Effective until (exclusive, optional)" value={end} onValueChange={setEnd} disabled={blocked} /></div>
    <Input id="intake-evidence-reference" label="Evidence reference" description="Identify the source record. References are recorded as text." required maxLength={256} value={reference} onValueChange={setReference} disabled={blocked} />
    <Input id="intake-evidence-locator" label="Evidence location" description="For example, a document page or source record number." required maxLength={512} value={locator} onValueChange={setLocator} disabled={blocked} />
    <DatePicker id="intake-observed-on" label="Evidence observed on" required value={observed} onValueChange={setObserved} disabled={blocked} />
    {failure && <p role="alert">{messages[failure]}</p>}
    <div className="intake-actions"><Button type="submit" disabled={blocked}>{pending ? 'Saving fact…' : 'Save fact revision'}</Button>
      <Button type="button" variant="ghost" disabled={pending} onClick={reload}>Reload current record</Button></div>
  </form>;
}

function FactHistory({ entityId, factKey }: { entityId: string; factKey: FactKey }) {
  const [history, setHistory] = useState<History>();
  const [failed, setFailed] = useState(false);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    let active = true;
    void readHistory(entityId, factKey, undefined).then(result => {
      if (!active) return;
      if (result.kind === 'ok') setHistory(result.value); else setFailed(true);
    });
    return () => { active = false; };
  }, [entityId, factKey]);
  return <section className="intake-history" aria-label="Fact history"><h4>History · {FACT_LABELS[factKey]}</h4>
    {failed && <p role="alert">History is unavailable. Reload the current record to retry.</p>}
    {history?.revisions.length === 0 && <p>No assertions recorded.</p>}
    <ol>{history?.revisions.map(row => <li key={row.id} data-fact-revision={row.revision}>
      <strong>Revision {row.revision} · {formatFact(row)}</strong>
      <p>Effective {row.effective_from}{row.effective_to ? ` until ${row.effective_to} (exclusive)` : ''}</p>
      <p>{row.evidence.reference} · {row.evidence.locator} · observed {row.evidence.observed_on}</p>
      <p>Recorded {row.recorded_at} by workspace member {row.recorded_by}</p>
    </li>)}</ol>
    {history?.next_before && <Button variant="ghost" disabled={pending} onClick={async () => {
      setPending(true);
      const next = await readHistory(entityId, factKey, history.next_before ?? undefined);
      if (next.kind === 'ok') setHistory({ revisions: [...history.revisions, ...next.value.revisions], next_before: next.value.next_before });
      else setFailed(true);
      setPending(false);
    }}>Older revisions</Button>}
  </section>;
}

export function IntakeWorkspace() {
  const [engagements, setEngagements] = useState<Engagement[]>([]);
  const [entityId, setEntityId] = useState('');
  const [profile, setProfile] = useState<EntityProfile>();
  const [factKey, setFactKey] = useState<FactKey>('legal_form');
  const [dateInput, setDateInput] = useState(today);
  const [on, setOn] = useState(today);
  const [attempt, setAttempt] = useState(0);
  const [listAttempt, setListAttempt] = useState(0);
  const [listPending, setListPending] = useState(true);
  const [name, setName] = useState('');
  const [entityName, setEntityName] = useState('');
  const [newEntity, setNewEntity] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  useEffect(() => {
    let active = true; setListPending(true);
    void listEngagements().then(result => {
      if (!active) return;
      if (result.kind === 'ok') {
        setEngagements(result.value);
        setEntityId(current => result.value.some(row => row.entities.some(entity => entity.id === current))
          ? current : result.value[0]?.entities[0]?.id ?? '');
      }
      else setMessage(messages[result.kind]);
      setListPending(false);
    });
    return () => { active = false; };
  }, [listAttempt]);
  useEffect(() => {
    let active = true; setProfile(undefined);
    if (entityId) void readEntity(entityId, on).then(result => {
      if (!active) return;
      if (result.kind === 'ok') setProfile(result.value); else setMessage(messages[result.kind]);
    });
    return () => { active = false; };
  }, [entityId, on, attempt]);
  const reload = () => { setMessage(''); setAttempt(n => n + 1); };
  return <Card className="intake-card" variant="outlined" sections="plain" aria-busy={listPending}><Card.Body>
    <div className="intake-heading"><div><span className="eyebrow">Start with the facts</span><h3>Engagements and client facts</h3></div>
      <Button variant="ghost" disabled={listPending} onClick={() => { reload(); setListAttempt(n => n + 1); }}>Reload facts</Button></div>
    <p>Record what you know, where it came from and when it applies. Earlier assertions remain in history.</p>
    {message && <p role="alert">{message}</p>}
    <details className="intake-create" open={!engagements.length}><summary>New engagement</summary>
      <form className="intake-form" aria-label="New engagement" autoComplete="off" onSubmit={async event => {
        event.preventDefault(); if (pending || listPending) return; setPending(true); setMessage('');
        const result = await createEngagement(name.trim(), entityName.trim());
        if (result.kind === 'ok') { setEngagements(rows => [...rows, result.value]); setEntityId(result.value.entities[0].id); setName(''); setEntityName(''); }
        else setMessage(messages[result.kind]);
        setPending(false);
      }}>
        <Input id="intake-engagement-name" label="Engagement name" required maxLength={128} value={name} onValueChange={setName} disabled={pending || listPending} />
        <Input id="intake-entity-name" label="Entity name" required maxLength={128} value={entityName} onValueChange={setEntityName} disabled={pending || listPending} />
        <Button type="submit" disabled={pending || listPending}>Create engagement</Button>
      </form>
    </details>
    {!!engagements.length && <Select id="intake-entity" label="Entity" value={entityId} disabled={pending || listPending}
      list={engagements.flatMap(row => row.entities.map(entity => ({ value: entity.id, label: `${row.name} · ${entity.name}` })))}
      onValueChange={next => { setEntityId(next); setMessage(''); }} />}
    {profile && <section className="intake-profile" aria-label="Client facts" data-entity-id={profile.id} data-entity-revision={profile.revision}>
      <h4>{profile.name}</h4>
      <form className="intake-date-pair" onSubmit={event => { event.preventDefault(); setOn(dateInput); }}>
        <DatePicker id="intake-as-of" label="As of date" required value={dateInput} onValueChange={setDateInput} />
        <Button type="submit" variant="ghost">View facts</Button>
      </form>
      {!!profile.stale_conclusions && <p role="status">{profile.stale_conclusions} dependent conclusion(s) need review after changed facts.</p>}
      <dl className="intake-facts">{FACT_KEYS.map(key => <div key={key} data-fact-key={key}>
        <dt>{FACT_LABELS[key]}</dt><dd>{formatFact(profile.facts[key])}</dd>
        <Button variant="ghost" size="sm" aria-label={`Review ${FACT_LABELS[key]}`} onClick={() => setFactKey(key)}>Review</Button>
      </div>)}</dl>
      <Select id="intake-fact-key" label="Fact to record or review" value={factKey}
        list={FACT_KEYS.map(key => ({ value: key, label: FACT_LABELS[key] }))} onValueChange={v => setFactKey(v as FactKey)} />
      <FactEditor key={`${profile.id}:${profile.revision}:${on}:${factKey}`} profile={profile} factKey={factKey} saved={reload} reload={reload} />
      <FactHistory key={`history:${profile.id}:${profile.revision}:${factKey}`} entityId={profile.id} factKey={factKey} />
      <details className="intake-create"><summary>Add entity to this engagement</summary>
        <form className="intake-form" onSubmit={async event => {
          event.preventDefault(); if (pending || listPending || !profile.can_edit) return; setPending(true); setMessage('');
          const result = await addEntity(profile.engagement_id, newEntity.trim());
          if (result.kind === 'ok') { setEngagements(rows => rows.map(row => row.id === profile.engagement_id ? { ...row, entities: [...row.entities, result.value] } : row)); setEntityId(result.value.id); setNewEntity(''); }
          else setMessage(messages[result.kind]);
          setPending(false);
        }}><Input label="Additional entity name" required maxLength={128} value={newEntity} onValueChange={setNewEntity} disabled={pending || listPending || !profile.can_edit} />
          <Button type="submit" disabled={pending || listPending || !profile.can_edit}>Add entity</Button></form>
      </details>
    </section>}
  </Card.Body></Card>;
}
