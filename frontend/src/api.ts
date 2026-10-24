export type SessionState =
  | { state: "checking" }
  | { state: "anonymous" }
  | { state: "authenticated"; userId: string }
  | { state: "unavailable" };

export async function readSession(fetcher: typeof fetch = fetch): Promise<SessionState> {
  try {
    const response = await fetcher("/api/session/", { credentials: "same-origin", cache: "no-store" });
    if (response.status === 401) return { state: "anonymous" };
    if (response.status !== 200) return { state: "unavailable" };
    const body: unknown = await response.json();
    if (typeof body !== "object" || body === null || !("user" in body)) return { state: "unavailable" };
    const user = body.user;
    if (typeof user !== "object" || user === null || !("id" in user) ||
        typeof user.id !== "string" || !/^[0-9]+$/.test(user.id)) return { state: "unavailable" };
    // Only identity needed for cosmetic preference scope leaves this adapter.
    // Finance/provider authority always stays on the server.
    return { state: "authenticated", userId: user.id };
  } catch {
    return { state: "unavailable" };
  }
}

export function themeStorageKey(session: SessionState): string {
  return session.state === "authenticated"
    ? `maipa.theme.user.${session.userId}`
    : "maipa.theme.signed-out";
}

type SignInResult = "signed_in" | "invalid_credentials" | "rate_limited" | "already_signed_in" | "unavailable";
const requestOptions = (): RequestInit => ({ credentials: "same-origin", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(10_000) });

async function csrfToken(fetcher: typeof fetch): Promise<string> {
  const response = await fetcher("/api/session/csrf/", requestOptions());
  if (response.status !== 200) throw Error("Session unavailable");
  const value: unknown = await response.json();
  if (typeof value !== "object" || value === null || !("csrf_token" in value) ||
      typeof value.csrf_token !== "string" || !/^[A-Za-z0-9]{64}$/.test(value.csrf_token)) throw Error("Session unavailable");
  return value.csrf_token;
}

export async function signIn(username: string, password: string, fetcher: typeof fetch = fetch): Promise<SignInResult> {
  if (!username || username.length > 150 || !password || password.length > 1024) return "invalid_credentials";
  try {
    const token = await csrfToken(fetcher);
    const response = await fetcher("/api/session/login/", { ...requestOptions(), method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": token }, body: JSON.stringify({ username, password }) });
    if (response.status === 401) return "invalid_credentials";
    if (response.status === 429) return "rate_limited";
    if (response.status === 409) return "already_signed_in";
    if (response.status !== 200) return "unavailable";
    const value: unknown = await response.json();
    if (typeof value !== "object" || value === null || !("user" in value) ||
        typeof value.user !== "object" || value.user === null || !("id" in value.user) ||
        typeof value.user.id !== "string" || !/^[0-9]+$/.test(value.user.id)) return "unavailable";
    return "signed_in";
  } catch { return "unavailable"; }
}

export async function signOut(fetcher: typeof fetch = fetch): Promise<boolean> {
  try {
    const token = await csrfToken(fetcher);
    const response = await fetcher("/api/session/logout/", { ...requestOptions(), method: "POST", headers: { "X-CSRFToken": token } });
    if (response.status !== 200) return false;
    const value: unknown = await response.json();
    return typeof value === "object" && value !== null && "status" in value && value.status === "signed_out";
  } catch { return false; }
}
