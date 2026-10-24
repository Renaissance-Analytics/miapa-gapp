import type { SessionState } from "./api.js";
import type { WhatsNewGroup } from "@particle-academy/fancy-whats-new";
import releases from "./releases.json" with { type: "json" };

export async function latestBuildVersion(fetcher: typeof fetch = fetch): Promise<string> {
  try {
    const response = await fetcher("/api/version/", {
      credentials: "same-origin", cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
    });
    if (response.status !== 200) throw Error();
    const body: unknown = await response.json();
    if (typeof body !== "object" || body === null || !("build_id" in body) ||
        typeof body.build_id !== "string" || !/^[0-9a-f]{64}$/.test(body.build_id)) throw Error();
    return body.build_id;
  } catch {
    throw new Error("Build check unavailable");
  }
}

export function updatesStorageKey(session: SessionState): string {
  if (session.state === "authenticated") return `maipa.updates.user.${session.userId}`;
  return session.state === "anonymous" ? "maipa.updates.signed-out" : "maipa.updates.unresolved";
}

// Pinned to this installed bundle: future builds' notes show after refreshing.
// Fancy owns unseen-group selection and the browser-local seen cursor.
export function loadReleaseNotes(_since: string | null): readonly WhatsNewGroup[] { return releases; }
