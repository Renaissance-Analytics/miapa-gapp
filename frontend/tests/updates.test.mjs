import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from 'node:fs';
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { selectNew } from "@particle-academy/fancy-whats-new";
import { latestBuildVersion, loadReleaseNotes, updatesStorageKey } from "../.test-build/updates.js";
import { BuildUpdates, ReleaseNotes } from "../.test-build/BuildUpdates.js";

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
  assert.equal(groups.length, 20);
  assert.equal(groups[0].id, "development-94");
  assert.equal(groups[0].cards.some(card=>card.title==="See your cash ahead beside the chat"),true);
  assert.equal(groups[1].id, "development-93");
  assert.equal(groups[1].cards.some(card=>card.title==="Plan ahead and try what-ifs"),true);
  assert.equal(groups[2].id, "development-92");
  assert.equal(groups[2].cards.some(card=>card.title==="Usable cash and a 13-week look ahead"),true);
  assert.equal(groups[3].id, "development-91");
  assert.equal(groups[3].cards.some(card=>card.title==="Consolidated totals for grouped businesses"),true);
  assert.equal(groups[4].id, "development-90");
  assert.equal(groups[4].cards.some(card=>card.title==="See several businesses together"),true);
  assert.equal(groups[5].id, "development-89");
  assert.equal(groups[5].cards.some(card=>card.title==="Fix: released statements are readable in the side panel"),true);
  assert.equal(groups[6].id, "development-88");
  assert.equal(groups[6].cards.some(card=>card.title==="See your released statements"),true);
  assert.equal(groups[7].id, "development-87");
  assert.equal(groups[7].cards.some(card=>card.title==="Fix: Maipa can ask for every kind of change again"),true);
  assert.equal(groups[8].id, "development-86");
  assert.equal(groups[8].cards.some(card=>card.title==="Released statements"),true);
  assert.equal(groups[9].id, "development-85");
  assert.equal(groups[9].cards.some(card=>card.title==="Workflows on a schedule"),true);
  assert.equal(groups[10].id, "development-84");
  assert.equal(groups[10].cards.some(card=>card.title==="Accruals that undo themselves"),true);
  assert.equal(groups[11].id, "development-83");
  assert.equal(groups[11].cards.some(card=>card.title==="Recurring entries"),true);
  assert.equal(groups[12].id, "development-82");
  assert.equal(groups[12].cards.some(card=>card.title==="See the month-end checklist beside the chat"),true);
  assert.equal(groups[13].id, "development-81");
  assert.equal(groups[13].cards.some(card=>card.title==="A month-end checklist before you close"),true);
  assert.equal(groups[14].id, "development-80");
  assert.equal(groups[14].cards.some(card=>card.title==="Long conversations keep working"),true);
  assert.equal(groups[15].id, "development-79");
  assert.equal(groups[15].cards.some(card=>card.title==="Discard a draft entry"),true);
  assert.equal(groups[16].id, "development-78");
  assert.equal(groups[16].cards.some(card=>card.title==="Why cash and profit differ"),true);
  assert.equal(groups[17].id, "development-77");
  assert.equal(groups[17].cards.some(card=>card.title==="See where your cash went"),true);
  assert.equal(groups[18].id, "development-76");
  assert.equal(groups[18].cards.some(card=>card.title==="Maipa’s building blocks are up to date"),true);
  assert.equal(groups[19].id, "development-75");
  assert.equal(groups[19].cards.some(card=>card.title==="See your income statement and balance sheet beside the chat"),true);
  assert.equal(groups[19].cards.some(card=>card.title==="Keep a report as a pinned view"),true);
  const archived = JSON.parse(readFileSync(new URL('../../docs/release-history.json', import.meta.url), 'utf8'));
  assert.deepEqual(archived.map(group => group.id), ['development-74','development-73','development-72','development-71','development-70','development-69','development-68','development-67','development-66','development-65','development-64','development-63','development-62','development-61','development-60','development-59','development-58','development-57','development-56','development-55','development-54','development-53','development-52','development-51','development-50','development-49','development-48','development-47','development-46','development-45','development-44','development-43','development-42','development-41','development-40','development-39','development-38','development-37','development-36','development-35','development-34','development-33','development-32','development-31','development-30','development-29','development-28','development-27','development-26','development-25','development-24','development-23','development-22','development-21','development-20','development-19','development-18','development-17']);
  assert.equal(new Set([...groups, ...archived].map(group => group.id)).size, 78);
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

test("release cards are a separate in-flow strip opened from the header button, and render nothing before they load", () => {
  let opened = 0;
  const controls = renderToStaticMarkup(React.createElement(BuildUpdates, { session: { state: "anonymous" }, buildId: "b".repeat(64), releaseLabel: "Fixture release", onOpen: () => { opened++; } }));
  assert.doesNotMatch(controls, /data-release-notes|fancy-whats-new/);
  const strip = renderToStaticMarkup(React.createElement(ReleaseNotes, { session: { state: "anonymous" }, open: false, onOpenChange: () => {} }));
  assert.equal(strip, '<div class="release-notes" data-release-notes="in-flow"></div>');
  assert.equal(renderToStaticMarkup(React.createElement(ReleaseNotes, { session: { state: "loading" }, open: true, onOpenChange: () => {} })), strip);
  assert.equal(opened, 0);
});
