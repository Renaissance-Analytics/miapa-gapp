import { useEffect, useState, type FormEvent } from 'react';
import { Button, Input } from '@particle-academy/react-fancy';
import { SignInForm } from './Authentication.js';
import { createAdmin, readFirstRun } from './onboarding.js';

export function FirstRunForm({ refresh }: { refresh: () => void }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    setPending(true); setMessage('');
    const result = await createAdmin(username, password, confirmation);
    setPassword(''); setConfirmation(''); setPending(false);
    if (result === 'created' || result === 'closed') { refresh(); return; }
    setMessage(result === 'invalid'
      ? 'Use a valid username and matching strong passwords of at least 12 characters. Avoid common passwords and your username.'
      : 'We couldn’t finish setup. Check your connection to Maipa and try again.');
  }
  return <div className="first-run">
    <h3>Set up Maipa on this computer</h3>
    <p>Create the administrator account for this local installation. After this step, you’ll set up your account in the conversation.</p>
    <form className="sign-in-form" aria-label="Create local administrator" onSubmit={submit}>
      <Input id="maipa-admin-username" name="username" label="Administrator username" autoComplete="username" required maxLength={150} value={username} onValueChange={setUsername} disabled={pending} />
      <Input id="maipa-admin-password" name="password" label="Password (at least 12 characters)" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} value={password} onValueChange={setPassword} disabled={pending} />
      <Input id="maipa-admin-confirmation" name="password-confirmation" label="Confirm password" type="password" autoComplete="new-password" required minLength={12} maxLength={1024} value={confirmation} onValueChange={setConfirmation} disabled={pending} />
      {message && <p className="auth-message" role="alert">{message}</p>}
      {message && <Button type="button" variant="ghost" onClick={refresh}>Check setup status again</Button>}
      <Button type="submit" className="sign-in-submit" disabled={pending}>{pending ? 'Creating administrator…' : 'Create administrator'}</Button>
    </form>
  </div>;
}

export function LocalAccess({ refresh }: { refresh: () => void }) {
  const [required, setRequired] = useState<boolean | null | undefined>(undefined);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    setRequired(undefined);
    void readFirstRun().then(value => { if (active) setRequired(value); });
    return () => { active = false; };
  }, [attempt]);
  if (required === true) return <FirstRunForm refresh={refresh} />;
  if (required === null) return <div role="alert"><p>We could not check local setup.</p><Button onClick={() => setAttempt(value => value + 1)}>Check local setup again</Button></div>;
  // A checking sign-in form preserves the familiar layout and remains protected
  // by server-owned membership; pristine installs cannot authenticate yet.
  return <><h3>Sign in to begin</h3><p>Your books, documents and conversations belong to your private workspace.</p>
    {required === undefined && <p role="status">Checking local setup…</p>}<SignInForm refresh={refresh} /></>;
}
