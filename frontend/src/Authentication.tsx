import { useState, type FormEvent } from "react";
import { Button, Input } from "@particle-academy/react-fancy";
import { signIn, signOut } from "./api.js";

export function SignInForm({ refresh }: { refresh: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    setPending(true); setMessage("");
    const result = await signIn(username, password);
    setPassword(""); setPending(false);
    if (result === "signed_in" || result === "already_signed_in") { refresh(); return; }
    setMessage(result === "invalid_credentials" ? "We could not sign you in. Check your details and workspace access."
      : result === "rate_limited" ? "Too many sign-in attempts. Try again in 15 minutes."
      : "Sign-in is unavailable. Try again.");
  }
  return <form className="sign-in-form" aria-label="Sign in to Maipa" onSubmit={submit}>
    <Input id="maipa-username" name="username" label="Username" autoComplete="username" required
      maxLength={150} value={username} onValueChange={setUsername} disabled={pending} />
    <Input id="maipa-password" name="password" label="Password" type="password" autoComplete="current-password" required
      maxLength={1024} value={password} onValueChange={setPassword} disabled={pending} />
    {message && <p className="auth-message" role="alert">{message}</p>}
    <Button type="submit" className="sign-in-submit" disabled={pending}>{pending ? "Signing in…" : "Sign in"}</Button>
  </form>;
}

export function SignOutButton({ refresh }: { refresh: () => void }) {
  const [pending, setPending] = useState(false);
  const [failed, setFailed] = useState(false);
  return <div className="sign-out-controls"><Button variant="ghost" disabled={pending} onClick={async () => {
    if (pending) return;
    setPending(true); setFailed(false);
    if (await signOut()) { refresh(); return; }
    setPending(false); setFailed(true);
  }}>{pending ? "Signing out…" : "Sign out"}</Button>
    {failed && <span role="alert">Sign-out is unavailable. Try again.</span>}
  </div>;
}
