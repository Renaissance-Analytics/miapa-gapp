import { useEffect, useRef, useState, type FormEvent } from 'react';
import { Button, Card, Input, Select } from '@particle-academy/react-fancy';
import { ACCESS_ROLES, readAdminUsers, saveLocalUser, type AccessFailure, type AccessRole,
  type AccessSnapshot, type LocalUser, type NewLocalUser } from './admin-users.js';

const roles = ACCESS_ROLES.map(value => ({ value, label: value[0].toUpperCase() + value.slice(1) }));
const messages: Record<AccessFailure, string> = {
  invalid: 'Check the username, password and confirmation. Passwords need at least 12 characters and must pass the password checks.',
  denied: 'Your current account cannot administer this workspace. Sign in with an enabled administrator account.',
  conflict: 'This change could not be applied. Reload access and review it before trying again. Keep at least one active administrator.',
  unavailable: 'We could not confirm the current access state. Reload it before making another change.',
};
type Save = (target: string | null, choice: NewLocalUser | Pick<LocalUser, 'role' | 'enabled'>) => Promise<void>;

function NewUser({ save, full }: { save: Save; full: boolean }) {
  const [username, name] = useState(''), [password, secret] = useState(''), [confirmation, confirm] = useState('');
  const [role, chooseRole] = useState<AccessRole>('viewer');
  function submit(event: FormEvent) {
    event.preventDefault();
    if (full) return;
    const choice = { username, password, confirmation, role };
    secret(''); confirm('');
    void save(null, choice);
  }
  return <form className="intake-form" aria-label="Create local user" onSubmit={submit}>
    <h4>Add a local user</h4><p>Give the user their login details privately. They will have their own account setup conversation.</p>
    <Input id="admin-new-username" label="Username" autoComplete="username" required maxLength={150} value={username} onValueChange={name} disabled={full} />
    <Input id="admin-new-password" label="Password" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} value={password} onValueChange={secret} disabled={full} />
    <Input id="admin-new-confirmation" label="Confirm password" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} value={confirmation} onValueChange={confirm} disabled={full} />
    <Select id="admin-new-role" label="Workspace role" list={roles} value={role} onValueChange={value => chooseRole(value as AccessRole)} disabled={full} />
    {full && <p role="status">This installation has reached its 200-user limit.</p>}
    <Button type="submit" disabled={full}>Create user</Button>
  </form>;
}

function Member({ user, save }: { user: LocalUser; save: Save }) {
  const [role, chooseRole] = useState(user.role), [enabled, enable] = useState(user.enabled);
  return <form className="intake-form admin-member" aria-label={`Access for ${user.username}`} data-member-id={user.id}
    onSubmit={event => { event.preventDefault(); void save(user.id, { role, enabled }); }}>
    <h4>{user.username}</h4>
    {!user.active && <p>This login is inactive.</p>}
    <Select id={`admin-role-${user.id}`} label={`Role for ${user.username}`} list={roles} value={role} onValueChange={value => chooseRole(value as AccessRole)} />
    <Select id={`admin-enabled-${user.id}`} label={`Workspace access for ${user.username}`} value={String(enabled)}
      list={[{ value: 'true', label: 'Enabled' }, { value: 'false', label: 'Disabled' }]} onValueChange={value => enable(value === 'true')} />
    <Button type="submit" disabled={role === user.role && enabled === user.enabled}>Save access for {user.username}</Button>
  </form>;
}

export function AdminUsers({ userId }: { userId: string }) {
  const [state, setState] = useState<AccessSnapshot>();
  const [phase, setPhase] = useState<'loading' | 'ready' | 'saving' | AccessFailure>('loading');
  const [notice, setNotice] = useState('');
  const generation = useRef(0);
  const pending = useRef(false);
  async function load() {
    const attempt = ++generation.current;
    pending.current = true;
    setState(undefined); setPhase('loading');
    const result = await readAdminUsers(userId);
    if (attempt !== generation.current) return;
    pending.current = false;
    if (result.kind === 'ok') { setState(result.value); setPhase('ready'); }
    else setPhase(result.kind);
  }
  useEffect(() => { void load(); return () => { generation.current++; }; }, [userId]);
  const save: Save = async (target, choice) => {
    if (!state || phase !== 'ready' || pending.current) return;
    pending.current = true;
    const attempt = ++generation.current;
    const previous = state;
    setState(undefined); setNotice(''); setPhase('saving');
    const result = await saveLocalUser(previous, target, choice);
    if (attempt !== generation.current) return;
    pending.current = false;
    if (result.kind === 'ok') { setNotice('Access change saved.'); await load(); }
    else setPhase(result.kind);
  };
  const busy = phase === 'loading' || phase === 'saving';
  return <Card variant="outlined" sections="plain" className="admin-users" aria-busy={busy} data-access-state={phase}><Card.Body>
    <div className="intake-heading"><div><span className="eyebrow">Local administration</span><h3>Users and access</h3></div>
      <Button variant="ghost" disabled={busy} onClick={() => { setNotice(''); void load(); }}>Reload users and access</Button></div>
    <p>Roles control workspace access. Client authority and private agent accounts keep their own scope.</p>
    {busy && <p role="status">{phase === 'saving' ? 'Saving access change…' : 'Loading current access…'}</p>}
    {notice && <p role="status">{notice}</p>}
    {!busy && phase !== 'ready' && <p role="alert">{messages[phase]}</p>}
    {state && phase === 'ready' && <div data-access-revision={state.revision}>
      <NewUser save={save} full={state.users.length >= 200} />
      <section aria-label="Local workspace users"><h4>Workspace users</h4>
        {state.users.map(user => <Member key={`${state.revision}:${user.id}`} user={user} save={save} />)}</section>
      <section aria-label="Recent access changes"><h4>Recent access changes</h4>
        {!state.events.length && <p>No access changes recorded yet.</p>}
        <ol>{state.events.map(event => <li key={event.revision}>Revision {event.revision}: member {event.actor_id} {event.kind === 'created' ? 'created' : 'updated'} member {event.subject_id}
          {' '}as {event.role}, {event.enabled ? 'enabled' : 'disabled'} · <time>{event.at}</time></li>)}</ol>
      </section>
    </div>}
  </Card.Body></Card>;
}
