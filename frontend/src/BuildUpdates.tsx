import type { SessionState } from "./api.js";
import { useState } from "react";
import { Button } from "@particle-academy/react-fancy";
import { AppUpdateAlert } from "@particle-academy/fancy-app-update";
import { WhatsNew } from "@particle-academy/fancy-whats-new";
import { latestBuildVersion, loadReleaseNotes, updatesStorageKey } from "./updates.js";
import { BUILD_ID, RELEASE_LABEL, UPDATES_ENABLED } from "./build.js";

export function BuildUpdates({ session, buildId = BUILD_ID, releaseLabel = RELEASE_LABEL }: { session: SessionState; buildId?: string; releaseLabel?: string }) {
  const [open, setOpen] = useState(false);
  const resolved = session.state === "anonymous" || session.state === "authenticated";
  return <>
    <div className="build-updates" data-maipa-build-id={buildId}>
      <span>{releaseLabel}</span>
      <span className="build-version" title={`Development build ${buildId}`}>Build {buildId === "unbuilt" ? "unbuilt" : buildId.slice(0, 12)}</span>
      <Button size="sm" variant="ghost" aria-label="What's new" disabled={!resolved} onClick={() => setOpen(true)}>What's new</Button>
    </div>
    <AppUpdateAlert enabled={UPDATES_ENABLED} currentVersion={buildId} latestVersion={latestBuildVersion} interval={30_000}
      render={({ refresh, dismiss }) => <aside className="update-notice" role="status" aria-live="polite" aria-label="App update">
        <strong>A newer build is available</strong>
        <p>Refresh Maipa when you're ready to load the changes.</p>
        <div><Button variant="ghost" size="sm" onClick={dismiss}>Later</Button><Button className="refresh-build" size="sm" onClick={refresh}>Refresh Maipa</Button></div>
      </aside>} />
    {resolved && <WhatsNew key={updatesStorageKey(session)} load={loadReleaseNotes} storageKey={updatesStorageKey(session)}
      missed="all" firstVisit="latest" open={open} onOpenChange={setOpen} color="indigo"
      labels={{ dismiss: "Got it", close: "Close updates", notice: "What's new in Maipa" }} />}
  </>;
}
