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
