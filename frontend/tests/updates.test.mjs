import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { selectNew } from "@particle-academy/fancy-whats-new";
import { latestBuildVersion, loadReleaseNotes, updatesStorageKey } from "../.test-build/updates.js";
import { BuildUpdates } from "../.test-build/BuildUpdates.js";

test("version checks fetch the same-origin uncached build with bounded lifetime and no redirects", async () => {
  let observed;
  const version = await latestBuildVersion(async (url, options) => {
    observed = { url, options };
    return new Response(JSON.stringify({ build_id: "a".repeat(64), private: "synthetic-secret" }));
  });
  assert.equal(version, "a".repeat(64));
  assert.equal(observed.url, "/api/version/");
  assert.equal(observed.options.cache, "no-store");
  assert.equal(observed.options.credentials, "same-origin");
  assert.equal(observed.options.redirect, "error");
  assert.equal(observed.options.signal instanceof AbortSignal, true);
});

test("failed or malformed version responses reject with a generic message instead of signalling an update", async () => {
  for (const fetcher of [
    async () => new Response("private-debug-value", { status: 503 }),
    async () => new Response("private-broken-json"),
    async () => new Response(JSON.stringify({ build_id: "" })),
    async () => new Response(JSON.stringify({ build_id: 42 })),
    async () => new Response(JSON.stringify({ build_id: "../private-value" })),
    async () => { throw new Error("private-network-value"); },
  ]) {
    await assert.rejects(() => latestBuildVersion(fetcher), { message: "Build check unavailable" });
  }
});

test("release notes describe the installed build and Fancy selects only unseen release groups per account", () => {
  const groups = loadReleaseNotes(null);
  assert.equal(groups.length >= 2, true);
  assert.equal(new Set(groups.map(group => group.id)).size, groups.length);
  assert.equal(groups.some(group => group.id === "development-2" && group.cards.some(card => card.title === "Track app updates")), true);
  assert.deepEqual(selectNew(groups, String(groups[1].id), { missed: "all", firstVisit: "latest" }).show.map(group => group.id), [groups[0].id]);
  assert.deepEqual(selectNew(groups, String(groups[0].id), { missed: "all", firstVisit: "latest" }).show, []);
  assert.notEqual(updatesStorageKey({ state: "authenticated", userId: "1" }), updatesStorageKey({ state: "authenticated", userId: "2" }));
  assert.notEqual(updatesStorageKey({ state: "anonymous" }), updatesStorageKey({ state: "authenticated", userId: "1" }));
});

test("the update surface always exposes the installed build and a named whats-new button without a false update alert", () => {
  const html = renderToStaticMarkup(React.createElement(BuildUpdates, { session: { state: "anonymous" }, buildId: "b".repeat(64), releaseLabel: "Fixture release" }));
  assert.match(html, /data-maipa-build-id="b{64}"/);
  assert.match(html, /Build b{12}/);
  assert.match(html, /Fixture release/);
  assert.match(html, /What(?:&#x27;|')s new/);
  assert.doesNotMatch(html, /Refresh Maipa|A newer build is available/);
});
