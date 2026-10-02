// LCE-037: renders the Control Center (synthetic data, mocked API) in Chromium at desktop and
// phone width; fails on console errors, empty views, horizontal overflow or a dialog that does not open.
// Run: PW_ROOT=$(npm root -g) CHROMIUM=/path/to/chrome [OUT=dir] node tests/ui/render.mjs
// Fictional data only.
import http from "node:http";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
const require = createRequire((process.env.PW_ROOT || process.cwd() + "/node_modules") + "/");
const { chromium } = require("playwright");
const UI = new URL("../../cloud/src/ui/", import.meta.url).pathname;
const now = new Date().toISOString();
const day = (n, t = "06:30:00") => new Date(Date.now() + n * 86400e3).toISOString().slice(0, 10) + "T" + t + "Z";
const ymd = (n) => day(n).slice(0, 10);
const TEXT = "Most ITSM teams think the barrier to agentic AI is the model.\n\nIt is not. It is data quality, governance and skills.\n\nWhat I keep seeing in operations teams:\n- tickets without clean categories\n- no owner for automation decisions\n\n#ITSM #AgenticAI #Automation";
const snap = {
  mode: "cloud", now, schedule_error: null,
  settings: { auto_publish: false, provider: "linkedin_api", timezone: "Europe/Berlin", api_version: "202609",
    person_urn: "urn:li:person:TestPerson1", visibility: "PUBLIC", token_present: true, emergency_stop: false,
    display_name: "Test Person", profile_url: "https://www.linkedin.com/in/test-person/", max_lateness_minutes: 180 },
  next_scheduled_publication: { consent_id: "c1", post_id: "20261008-demo-b", slot_id: "s1", slot_utc: day(3) },
  upcoming_slots: [{ slot_id: "s1", utc: day(3), local: "x" }, { slot_id: "s2", utc: day(5, "08:00:00"), local: "Sat 10:00" }],
  posts: [{ post_id: "20261008-demo-b", state: "READY_TO_PUBLISH", text: TEXT, approved_hash: "a".repeat(64), plan_date: ymd(3), image: null },
          { post_id: "20261012-demo-c", state: "READY_TO_PUBLISH", text: "Second approved fictional post.\n\n#Ops", approved_hash: "b".repeat(64), plan_date: ymd(7), image: { sha256: "c", bytes: 20480 } }],
  consents: [{ consent_id: "c1", post_id: "20261008-demo-b", slot_id: "s1", slot_utc: day(3), status: "active" }],
  jobs: [], publications: [{ post_id: "20260920-old", state: "published", url: "https://www.linkedin.com/feed/update/urn:li:share:1/", remote_id: "urn:li:share:1", published_at: day(-5), verified_by: "api_response", idempotency_key: "k".repeat(64) }],
  events: [{ at: day(-1), event: "consent.created", actor: "owner@example.com", post_id: "20261008-demo-b", detail: '{"consent_id":"c1"}' }],
  decisions: [{ decision_id: "d-1", action: "approve", post_id: "20261006-demo-a", status: "pending", created_at: day(0), created_by: "owner@example.com", payload: {} },
              { decision_id: "d-2", action: "edit", post_id: "20261010-demo-d", status: "refused", result: "the text changed", created_at: day(-1), created_by: "owner@example.com", payload: {} }],
};
const pipe = { meta: { mirror: { received_at: day(0) } },
  posts: [{ post_id: "20261006-demo-a", state: "AWAITING_APPROVAL", text: TEXT, actual_hash: "d".repeat(64), plan_date: ymd(1), topic: "Agentic AI in ITSM", qa: { status: "passed" }, image: null, sources: [{ url: "https://example.com/report", title: "Example report" }], history: [{ at: day(-2), state: "AWAITING_APPROVAL" }] },
          { post_id: "20261008-demo-b", state: "READY_TO_PUBLISH", text: TEXT, actual_hash: "a".repeat(64), plan_date: ymd(3), topic: "B", image: { kind: "diagram", file: "diagram.png", alt_text: "A diagram", sha256: "e" } },
          { post_id: "20261010-demo-d", state: "NEEDS_REVISION", text: "Needs work.", actual_hash: "f".repeat(64), plan_date: ymd(4), format: "video", topic: "D" }],
  calendar: [{ date: ymd(1), topic: "Agentic AI in ITSM", status: "awaiting_approval", draft_ref: "20261006-demo-a" },
             { date: ymd(3), topic: "B", status: "ready_to_publish", draft_ref: "20261008-demo-b" },
             { date: ymd(4), topic: "D", status: "in_progress", draft_ref: "20261010-demo-d", format: "video" },
             { date: ymd(6), topic: "A planned topic without a post", status: "planned", format: "document" }] };
pipe.voice = { version: 2, tone: ["Natural", "Practical", "Direct"], point_of_view: { person: "first", first_person: "Observations and opinions in first person; experience only from PUBLIC stories" },
  individual_voice: "One practitioner speaking, never a company page", technical_depth: "mixed", evidence: "Every number sourced", hashtag_policy: { max: 3, placement: "end" },
  avoid_phrases: [], avoid_patterns: ["generic closers"], review: { status: "pending_owner_review", fields: ["technical_depth", "point_of_view"] },
  objectives: [{ id: "demonstrate-expertise", label: "Demonstrate practical expertise" }, { id: "share-lesson", label: "Share a lesson learned" }] };
pipe.posts[0].objective = "demonstrate-expertise";
pipe.posts[0].humanization = { at: day(-2), by: "pipeline session", source: "session", voice_version: 2, profile_current: true, checklist: { passed: 10, failed: 0, review: 2 } };
pipe.posts[0].duplicate = { status: "passed" };
pipe.posts.push({ post_id: "20260929-old-approved", state: "APPROVED", text: "An approved fictional post whose date passed.\n\nBody.", actual_hash: "9".repeat(64), plan_date: ymd(-3), topic: "Old", image: null, sources: [{ url: "https://example.com" }], qa: { status: "passed" }, duplicate: { status: "passed" } });
pipe.meta.engine = { version: "0.1.0", commit: "abcdef1234" };
pipe.calendar.unshift({ date: ymd(-3), topic: "Old", status: "approved", draft_ref: "20260929-old-approved" });
snap.preview_media = [{ post_id: "20261008-demo-b", sha256: "e", bytes: 100, mime: "image/png" }];
pipe.posts[1].image = { kind: "diagram", media_status: "attached", file: "image.png", sha256: "e", width: 1200, height: 1200, bytes: 61440, mime: "image/png",
  alt_text: "A fictional checklist diagram", relation: "restates the post's checklist", provenance: { origin: "own_creation", usage: "owned", generation: { method: "lce image diagram" } },
  decided_by: "agent", decided_at: day(-1) };
pipe.posts[0].image = { kind: "none", media_status: "text_only", text_only_reason: "text_carries_point", rationale: "One figure carries it." };
// LCE-040: today's refresh updated demo-a (approval reopened); demo-b has only an old check and no cloud row.
const berlinToday = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Berlin" }).format(new Date());
pipe.posts[0].freshness = { latest: { checked_at: now, check_date: berlinToday, mode: "update", by: "session", decision: "updated",
  status: "update_awaiting_approval", material_change: true, reason: "source revised its figure", approval_effect: "invalidated",
  content_hash: "d".repeat(64), content_hash_before: "0".repeat(64), image_sha256: null, image_sha256_before: null,
  sources: [{ url: "https://example.com/report", status: "checked_by_session" }], claims: [],
  media: { status: "text_only", note: "text_carries_point" }, steps: { humanization: { passed: 10, failed: 0, review: 2 }, qa: "passed", duplicate: { status: "passed", compared_against: 12, exact: 0, near: 0 } } }, history: [] };
pipe.posts[0].freshness.history = [{ ...pipe.posts[0].freshness.latest, mode: "check", decision: "update_required", status: "update_required" }, pipe.posts[0].freshness.latest];
pipe.posts[1].freshness = { latest: { checked_at: day(-1), check_date: ymd(-1), mode: "check", by: "workflow", decision: "unchanged", status: "current", content_hash: "a".repeat(64), sources: [], claims: [], media: { status: "still_relevant" } }, history: [] };
snap.freshness = [];
// LCE-041: demo-a was refreshed (v1 kept, with its image); the old approved post has a pending refresh request;
// demo-b's diagram carries an accepted relevance record.
pipe.posts[0].refresh = { completed_at: day(0), by: "session", reason: "conceptual visual instead of a text checklist", outcome: "refreshed", previous_version: 1 };
pipe.posts[0].versions = [{ version: 1, created_at: day(-1), by: "session", reason: "before refresh: conceptual visual", state: "AWAITING_APPROVAL",
  content_hash: "0".repeat(64), hook: "An older fictional hook that the refresh replaced.", image_sha256: "1".repeat(64), image_file: "image.png",
  media: { kind: "diagram", concept: null, visual_type: null, media_decision: null, alt_text: "Old checklist diagram" }, approval_state: "pending",
  files: ["post.md", "image.png"], text: "An older fictional hook that the refresh replaced.\n\nOld body." }];
pipe.posts[3].refresh_request = { requested_at: day(0), requested_by: "cloud-access:owner@example.com", note: "image repeats the text" };
pipe.posts[1].image.media_relevance = { concept: "rules before models", visual_type: "flow", relevance_reason: "shows the decision order the post argues for",
  copied_post_text_ratio: 0.05, factual_claims: [], source_requirements: [], media_decision: "accepted", problems: [] };
snap.version_media = [{ post_id: "20261006-demo-a", version: 1, sha256: "1".repeat(64), bytes: 100, mime: "image/png" }];
const identity = { ok: true, status: "verified", person_urn: "urn:li:person:TestPerson1", configured_person_urn: "urn:li:person:TestPerson1", person_urn_matches: true, api_version: "202609", api_version_valid: true };
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");
const files = { "/": ["index.html", "text/html"], "/app.js": ["app.txt", "text/javascript"], "/lib.js": ["lib.txt", "text/javascript"], "/app.css": ["app.css", "text/css"] };
const srv = http.createServer((req, res) => {
  const u = new URL(req.url, "http://x");
  const j = (o, s = 200) => { res.writeHead(s, { "content-type": "application/json" }); res.end(JSON.stringify(o)); };
  if (files[u.pathname]) { const [f, t] = files[u.pathname]; res.writeHead(200, { "content-type": t, "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" }); return res.end(readFileSync(UI + f)); }
  // LCE-041: an expired Access session — the edge redirects API calls to its login page (another origin)
  if (req.method === "POST" && u.pathname === "/api/decisions") { res.writeHead(302, { location: "https://access.example.invalid/login" }); return res.end(); }
  if (u.pathname === "/api/snapshot") return j(snap);
  if (u.pathname === "/api/pipeline") return j(pipe);
  if (u.pathname === "/api/migrations") return j({ applied: ["0001", "0002", "0003", "0004"], pending: [] });
  if (u.pathname === "/api/linkedin/identity") return j(identity);
  if (u.pathname.endsWith("/image") && u.pathname.includes("demo-c")) { res.writeHead(302, { location: "/cdn-cgi/access/login" }); return res.end(); }
  if (u.pathname.endsWith("/image")) { res.writeHead(200, { "content-type": "image/png" }); return res.end(PNG); }
  j({ error: "not found" }, 404);
}).listen(0);
const port = srv.address().port;
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
const errors = [];
for (const [name, vp] of [["desktop", { width: 1280, height: 900 }], ["mobile", { width: 390, height: 844 }]]) {
  const page = await browser.newPage({ viewport: vp });
  page.on("console", (m) => { if (m.type() === "error") errors.push(`${name}: ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`${name} pageerror: ${e.message}`));
  for (const view of ["overview", "upcoming", "posts", "post/20261006-demo-a", "post/20261008-demo-b", "post/20260929-old-approved", "test/20261012-demo-c", "history", "system"]) {
    await page.goto(`http://127.0.0.1:${port}/#${view}`);
    await page.waitForTimeout(400);
    const text = await page.locator("main").innerText();
    if (!text.trim() || text.includes("Could not load")) errors.push(`${name} ${view}: empty or failed: ${text.slice(0, 200)}`);
    if (await page.locator("main a img").count()) errors.push(`${name} ${view}: an image is wrapped in a link`);
    if (text.includes("/api/posts/")) errors.push(`${name} ${view}: image URL rendered as text`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    if (overflow) errors.push(`${name} ${view}: horizontal overflow`);
    if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-${view.replace("/", "_")}.png`, fullPage: true });
  }
  // technical details of an old post (no humanization/media records) must be informative
  await page.goto(`http://127.0.0.1:${port}/#post/20260929-old-approved`);
  await page.waitForTimeout(300);
  await page.locator("details.tech summary").click();
  const tech = await page.locator("details.tech").innerText();
  for (const want of ["Content hash", "Pipeline", "Voice profile", "Humanization", "no record", "Media", "No media decision recorded", "Sources", "QA", "Duplicate check"]) {
    if (!tech.includes(want)) errors.push(`${name}: technical details lack ${want}`);
  }
  // Upcoming: the image post shows its real thumbnail first in the card, square and uncropped
  await page.goto(`http://127.0.0.1:${port}/#upcoming`);
  await page.waitForTimeout(500);
  const row = page.locator(".row.has-media").first();
  const ubox = await row.locator("img.card-img").boundingBox();
  const order = await row.evaluate((r) => [...r.querySelector(".row-main").children].map((c) => c.className));
  if (!ubox || ubox.width < 150 || Math.abs(ubox.width - ubox.height) > 2) errors.push(`${name}: upcoming thumbnail not a visible square (${JSON.stringify(ubox)})`);
  if (order[0] !== "card-media" || order[1] !== "row-title") errors.push(`${name}: upcoming card order is ${order}`);
  if (await page.locator(".row:not(.has-media) img").count()) errors.push(`${name}: text-only row shows an image area`);
  // an image that cannot load shows a visible diagnostic, not bare alt text
  await page.goto(`http://127.0.0.1:${port}/#test/20261012-demo-c`);
  await page.waitForTimeout(800);
  if (!(await page.locator("main").innerText()).includes("could not be displayed")) errors.push(`${name}: failed image has no visible diagnostic`);
  // a loading image is a visible <img> of real size, not a link
  await page.goto(`http://127.0.0.1:${port}/#post/20261008-demo-b`);
  await page.waitForTimeout(500);
  const box = await page.locator(".media-thumb").boundingBox();
  const inLink = await page.locator("a .media-thumb, a img.media").count();
  if (!box || box.width < 150 || box.height < 150) errors.push(`${name}: media thumbnail not visible (${JSON.stringify(box)})`);
  if (inLink) errors.push(`${name}: post-page image wrapped in a link`);
  // LCE-040: freshness state, approval consequence, evidence and the publish gate are visible
  await page.goto(`http://127.0.0.1:${port}/#upcoming`);
  await page.waitForTimeout(400);
  const up = await page.locator("main").innerText();
  for (const want of ["Freshness: Update requires approval", "Freshness: Not checked", "Publish gate: no fresh check received", "invalidated by refresh"]) {
    if (!up.includes(want)) errors.push(`${name}: upcoming lacks "${want}"`);
  }
  await page.goto(`http://127.0.0.1:${port}/#post/20261006-demo-a`);
  await page.waitForTimeout(300);
  const fc = page.locator("section.card", { hasText: "Same-day freshness" });
  await fc.locator("summary", { hasText: "Evidence" }).click();
  const ftext = await fc.innerText();
  for (const want of ["Update requires approval", "invalidated", "Sources checked", "example.com/report", "Image re-evaluation", "Re-run after update", "Recent checks (2)"]) {
    if (!ftext.toLowerCase().includes(want.toLowerCase())) errors.push(`${name}: freshness card lacks "${want}"`);
  }
  if (process.env.OUT) await fc.screenshot({ path: `${process.env.OUT}/${name}-freshness.png` });
  // LCE-041: Refresh — refreshed state, previous → current with the previous image, confirmation dialog
  await page.goto(`http://127.0.0.1:${port}/#post/20261006-demo-a`);
  await page.waitForTimeout(500);
  const pa = await page.locator("main").innerText();
  for (const want of ["Refreshed · Awaiting approval", "Versions", "Previous version · v1", "An older fictional hook", "Current version", "Compare with v1"]) {
    if (!pa.toLowerCase().includes(want.toLowerCase())) errors.push(`${name}: refreshed post lacks "${want}"`);
  }
  const vimg = await page.locator("img.version-img").first().evaluate((i) => ({ w: i.naturalWidth, src: i.getAttribute("src") }));
  if (!vimg.w || !vimg.src.includes("/versions/1/image")) errors.push(`${name}: previous version image not loaded ${JSON.stringify(vimg)}`);
  if (process.env.OUT) await page.locator("section.card", { hasText: "Previous version" }).screenshot({ path: `${process.env.OUT}/${name}-versions.png` });
  const ctl = await page.locator("section.card", { hasText: "Controls" }).innerText();
  const corder = ["Approve", "Refresh", "Edit", "Reschedule", "Skip", "Reject"].map((b) => ctl.indexOf(b));
  if (corder.some((x) => x < 0) || corder.some((x, k) => k && x < corder[k - 1])) errors.push(`${name}: controls not in order Approve | Refresh | Edit | Reschedule | Skip | Reject (${ctl})`);
  await page.locator("section.card", { hasText: "Controls" }).getByRole("button", { name: "Refresh", exact: true }).click();
  await page.waitForTimeout(200);
  const dlg = await page.locator("dialog[open]").innerText().catch(() => "");
  if (!dlg.includes("Refresh this post?") || !dlg.includes("preserved in History") || !dlg.includes("require your approval again")) errors.push(`${name}: refresh dialog wrong: ${dlg.slice(0, 200)}`);
  if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-refresh-dialog.png` });
  // confirming while the Access session has expired: a clear banner, never a bare "Failed to fetch"
  await page.locator("dialog[open]").getByRole("button", { name: "Refresh", exact: true }).click();
  await page.waitForTimeout(500);
  const banner = await page.locator("#session-banner").innerText().catch(() => "");
  const toastText = await page.locator("#toast").innerText().catch(() => "");
  if (!banner.includes("nothing was recorded") || !banner.includes("Sign in again")) errors.push(`${name}: no session-expired banner (${banner})`);
  if (toastText.includes("Failed to fetch") || !toastText.includes("expired")) errors.push(`${name}: unclear refusal: ${toastText}`);
  if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-session-expired.png` });
  await page.evaluate(() => document.getElementById("session-banner")?.remove());
  // a scheduled (cloud-queued) post has no Refresh; the requested one says so
  await page.goto(`http://127.0.0.1:${port}/#upcoming`);
  await page.waitForTimeout(400);
  const upr = await page.locator("main").innerText();
  if (!upr.includes("Refresh requested")) errors.push(`${name}: upcoming lacks the refresh request`);
  const queuedRow = page.locator(".row.status-scheduled").first();
  if (await queuedRow.getByRole("button", { name: "Refresh" }).count()) errors.push(`${name}: a scheduled post offers Refresh`);
  // media relevance in the media card
  await page.goto(`http://127.0.0.1:${port}/#post/20261008-demo-b`);
  await page.waitForTimeout(400);
  const mc = await page.locator("section.card", { hasText: "Visual concept" }).innerText().catch(() => "");
  for (const want of ["Relevant to the post", "rules before models", "flow", "5%", "Image hash"]) if (!mc.includes(want)) errors.push(`${name}: media card lacks "${want}"`);
  // open a dialog
  await page.goto(`http://127.0.0.1:${port}/#post/20261006-demo-a`);
  await page.waitForTimeout(300);
  await page.getByRole("button", { name: "Approve", exact: true }).first().click();
  await page.waitForTimeout(200);
  if (!(await page.locator("dialog[open]").count())) errors.push(`${name}: approve dialog did not open`);
  if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-dialog.png` });
  await page.close();
}
await browser.close(); srv.close();
const real = errors.filter((e) => !e.includes("404 (Not Found)"));   // favicon
console.log(real.length ? "ERRORS:\n" + real.join("\n") : "OK no errors");
process.exit(real.length ? 1 : 0);
