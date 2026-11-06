import { useEffect, useRef, useState } from 'react';
import { Button, Callout, Card, Form, Input, Select } from '@particle-academy/react-fancy';
import { readClientCatalog, saveClientCatalog, type CatalogCommand, type CatalogFailure, type CatalogSnapshot } from './client-catalog.js';
import { SharedFormController, type FormBinding, type FormState, type NativeFormActivity } from './shared-form.js';

type Save = (command: CatalogCommand) => Promise<boolean>;
const messages: Record<CatalogFailure, string> = {
  invalid: 'Check the display names and client limits. Reload client setup before making another change.',
  denied: 'Your current account cannot manage these client records. Sign in with an enabled administrator or preparer account.',
  conflict: 'Client setup changed or the selected record is no longer available. Reload it and review the current records before saving.',
  unavailable: 'We could not confirm the current client setup. Reload it before making another change.',
};

function NewClient({ save, full, controller, shared, activity }: { save: Save; full: boolean; controller: SharedFormController | null;
  shared: FormState | null; activity?: NativeFormActivity }) {
  const [name, setName] = useState(''), [entity, setEntity] = useState('');
  const blocked = full || !!shared && !['ready', 'saving'].includes(shared.phase);
  async function submit() {
    if (blocked) return;
    if (controller) {
      if (!await controller.flush() || !controller.state.saved) return;
      const submitted = structuredClone(controller.state.saved), values = controller.state.values;
      if (await save({ action: 'create_engagement', name: values.name.trim(), entity_name: values.entity_name.trim() })) await controller.clear(submitted);
    } else if (await save({ action: 'create_engagement', name: name.trim(), entity_name: entity.trim() })) { setName(''); setEntity(''); }
  }
  return <Form className="intake-form" aria-label="Create client" data-shared-form={shared?.saved?.id} data-form-phase={shared?.phase}
    data-form-revision={shared?.saved?.revision} onSubmit={event => {
    event.preventDefault(); void submit();
  }}><h4>Add a client</h4>
    <Input id="catalog-new-client" label="Client / engagement name" maxLength={128} required value={shared?.values.name ?? name}
      onValueChange={value => controller ? controller.change('name', value) : setName(value)} disabled={blocked} />
    <Input id="catalog-initial-entity" label="Initial entity name" maxLength={128} required value={shared?.values.entity_name ?? entity}
      onValueChange={value => controller ? controller.change('entity_name', value) : setEntity(value)} disabled={blocked} />
    {shared?.phase === 'saving' && <small role="status">Saving pending input…</small>}
    {shared?.phase === 'loading' && <small role="status">Loading shared input…</small>}
    {shared && ['conflict', 'unavailable'].includes(shared.phase) && <Callout color="amber" role="alert">
      {shared.phase === 'conflict' ? 'The shared input changed. Your unsent values are kept here.' : 'We could not confirm the saved input. Your values are kept here.'}
      <Button size="sm" variant="ghost" onClick={() => { void controller?.load(true); }}>Reload shared input</Button></Callout>}
    {shared?.phase === 'closed' && <small role="status">This shared form is no longer available. Ask Maipa to open it again.</small>}
    {activity && activity.form_id === shared?.saved?.id && activity.revision === shared.saved.revision && <Callout color="blue" role="status" data-native-form-activity={activity.id}>Maipa updated pending values.</Callout>}
    {full && <p role="status">You have reached the 100-client limit.</p>}
    <Button type="submit" disabled={blocked}>Create client</Button>
  </Form>;
}

function Rename({ id, name, kind, save }: { id: string; name: string; kind: 'client' | 'entity'; save: Save }) {
  const [value, setValue] = useState(name);
  return <form className="intake-form" aria-label={`Rename ${kind} ${name}`} onSubmit={event => {
    event.preventDefault();
    void save(kind === 'client' ? { action: 'rename_engagement', engagement_id: id, name: value.trim() }
      : { action: 'rename_entity', entity_id: id, name: value.trim() });
  }}>
    <Input id={`catalog-name-${kind}-${id}`} label={`${kind === 'client' ? 'Client' : 'Entity'} display name`}
      required maxLength={128} value={value} onValueChange={setValue} />
    <Button type="submit" disabled={value.trim() === name}>Save {kind} name for {name}</Button>
  </form>;
}

function AddEntity({ engagement, full, save }: { engagement: string; full: boolean; save: Save }) {
  const [name, setName] = useState('');
  return <form className="intake-form" aria-label="Add client entity" onSubmit={event => {
    event.preventDefault(); if (!full) void save({ action: 'create_entity', engagement_id: engagement, name: name.trim() });
  }}><h4>Add an entity</h4>
    <Input id="catalog-add-entity" label="New entity name" required maxLength={128} value={name} onValueChange={setName} disabled={full} />
    {full && <p role="status">This client has reached its 100-entity limit.</p>}
    <Button type="submit" disabled={full}>Add entity</Button>
  </form>;
}

export function ClientCatalog({ userId, binding, refreshRevision, activity }: { userId: string; binding?: FormBinding;
  refreshRevision?: number; activity?: NativeFormActivity }) {
  const [state, setState] = useState<CatalogSnapshot>();
  const [shared, setShared] = useState<FormState | null>(binding ? { phase: 'loading', saved: null, values: { name: '', entity_name: '' } } : null);
  const form = useRef<SharedFormController | null>(null);
  useEffect(() => {
    if (!binding) { setShared(null); return; }
    const controller = new SharedFormController(binding, setShared); form.current = controller;
    setShared(controller.state); void controller.load();
    const timer = setInterval(() => { void controller.load(); }, 1500);
    return () => { clearInterval(timer); controller.dispose(); form.current = null; };
  }, [userId, binding?.accountId, binding?.leaseId, binding?.componentId]);
  useEffect(() => { void form.current?.load(); }, [refreshRevision]);
  const [phase, setPhase] = useState<'loading' | 'ready' | 'saving' | CatalogFailure>('loading');
  const [notice, setNotice] = useState('');
  const generation = useRef(0), pending = useRef(false);
  async function load(selected?: string) {
    const attempt = ++generation.current;
    pending.current = true; setState(undefined); setPhase('loading');
    const result = await readClientCatalog(userId, selected);
    if (attempt !== generation.current) return;
    pending.current = false;
    if (result.kind === 'ok') { setState(result.value); setPhase('ready'); }
    else setPhase(result.kind);
  }
  useEffect(() => { void load(); return () => { generation.current++; }; }, [userId]);
  const save: Save = async command => {
    if (!state || phase !== 'ready' || pending.current) return false;
    pending.current = true;
    const attempt = ++generation.current, previous = state;
    setState(undefined); setNotice(''); setPhase('saving');
    const result = await saveClientCatalog(previous, command);
    if (attempt !== generation.current) return false;
    pending.current = false;
    if (result.kind === 'ok') { setNotice('Client setup saved.'); await load(result.value.engagement_id); return true; }
    else setPhase(result.kind);
    return false;
  };
  const busy = phase === 'loading' || phase === 'saving';
  const selected = state?.engagements.find(row => row.id === state.selected_engagement_id);
  return <Card variant="outlined" sections="plain" aria-busy={busy} data-catalog-state={phase}><Card.Body>
    <div className="intake-heading"><div><span className="eyebrow">Your client records</span><h3>Client setup</h3></div>
      <Button variant="ghost" disabled={busy} onClick={() => { setNotice(''); void load(); }}>Reload client setup</Button></div>
    <p>Manage your clients and entity display names. Open Client facts to review financial setup and evidence.</p>
    {busy && <p role="status">{phase === 'saving' ? 'Saving client setup…' : 'Loading client setup…'}</p>}
    {notice && <p role="status">{notice}</p>}
    {!busy && phase !== 'ready' && <p role="alert">{messages[phase]}</p>}
    {state && phase === 'ready' && <div data-catalog-revision={state.revision}>
      <NewClient save={save} full={state.engagements.length >= 100} controller={form.current} shared={shared} activity={activity} />
      {!state.engagements.length && <p>No client records yet. Create a client or continue Account setup in the conversation.</p>}
      {selected && <section aria-label="Selected client setup">
        <Select id="catalog-client" label="Select client" value={selected.id}
          list={state.engagements.map(row => ({ value: row.id, label: row.name }))}
          onValueChange={value => { if (!pending.current && value !== selected.id) { setNotice(''); void load(value); } }} />
        <Rename key={`${state.revision}:${selected.id}`} id={selected.id} name={selected.name} kind="client" save={save} />
        <section aria-label="Client entities"><h4>Entities</h4>
          {state.entities.map(row => <Rename key={`${state.revision}:${row.id}`} id={row.id} name={row.name} kind="entity" save={save} />)}
        </section>
        <AddEntity key={`add:${selected.id}:${state.revision}`} engagement={selected.id} save={save} full={selected.entity_count >= 100} />
      </section>}
      <section aria-label="Recent client setup changes"><h4>Recent client setup changes</h4>
        {!state.events.length && <p>No client setup changes recorded yet.</p>}
        <ol>{state.events.map(event => <li key={event.revision}>
          {({ create_engagement: 'Client created', create_entity: 'Entity added', rename_engagement: 'Client name changed', rename_entity: 'Entity name changed' })[event.action]}
          {' · '}<time>{event.at}</time>
        </li>)}</ol>
      </section>
    </div>}
  </Card.Body></Card>;
}
