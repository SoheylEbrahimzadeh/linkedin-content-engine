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
// LCE-050: Content Radar and freshness plan (fictional items only)
pipe.radar = { configured: true, total: 41, relevant: 23, new_today: 3, by_pillar: { "itsm-it-operations": 9, "ai-business-automation": 8, "digital-transformation": 6 },
  items: [
    { id: "r-1", title: "Fictional vendor adds approval steps to ticket routing rules", url: "https://example.com/news/routing", source: "Example Vendor Newsroom", quality: "vendor",
      published_at: day(0, "05:00:00"), first_seen: day(0, "06:00:00"), age_hours: 2, pillar: "itsm-it-operations", relevance: 0.75, matched_terms: ["itsm", "service management"],
      excerpt: "A fictional release note about routing and approvals for service desks.", used_by: null, related_post: "20261006-demo-a", recommended_for: null },
    { id: "r-2", title: "Survey (fictional): most automation pilots stall at the data stage", url: "https://example.org/survey", source: "Example Analyst Blog", quality: "analyst",
      published_at: day(-1), first_seen: day(-1), age_hours: 26, pillar: "ai-business-automation", relevance: 0.5, matched_terms: ["automation", "ai agents"],
      excerpt: "Fictional excerpt for the render test.", used_by: null, related_post: null, recommended_for: ymd(4) },
    { id: "r-3", title: "r/fictional: how we run blameless incident reviews", url: "https://example.net/r/fictional/1", source: "r/fictional", quality: "community",
      published_at: day(-3), first_seen: day(-3), age_hours: 74, pillar: "itsm-it-operations", relevance: 0.25, matched_terms: ["itsm"], excerpt: "", used_by: "20261008-demo-b", related_post: null, recommended_for: null }],
  sources: [{ id: "vendor", name: "Example Vendor Newsroom", status: "ok", http: 200, entries: 20, new: 2, checked_at: day(0) },
            { id: "blocked", name: "Example Blocked Feed", status: "unreachable", http: 403, entries: 0, new: 0, checked_at: day(0) }] };
pipe.freshness_plan = { lead_hours: 36, horizon_days: 30, candidate_lead_days: 7,
  work: [{ kind: "research", post_id: "20261006-demo-a", plan_date: ymd(1), key: "research:a" }, { kind: "slot", plan_date: ymd(6), key: "slot:x", started: { fired_at: day(0) } }],
  slots: { [ymd(1)]: { post_id: "20261006-demo-a", slot_utc: day(1), window_opens_at: day(-1), in_window: true, next_check: day(0, "18:00:00"), last_check: day(0, "05:00:00"),
    last_check_status: "current", last_research: null, packet: { built_at: day(0, "06:00:00"), fresh: 4, older: 2 },
    new_developments: [{ id: "r-1", title: "Fictional vendor adds approval steps to ticket routing rules", url: "https://example.com/news/routing", source: "Example Vendor Newsroom" }] } } };
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
// LCE-043: a refreshed post with a real Commons photo, its rights record and the image search
pipe.posts.push({ post_id: "20261014-demo-e", state: "AWAITING_APPROVAL", text: "A fictional post about operations rooms.\n\nImage: Jane Example, CC BY-SA 4.0, via Wikimedia Commons", actual_hash: "e".repeat(64), plan_date: ymd(9), topic: "E",
  image: { kind: "source_image", file: "image.png", sha256: "5".repeat(64), width: 1600, height: 1067, bytes: 240000, mime: "image/png", media_status: "attached",
    alt_text: "Photo of server racks in a data center operations room", relation: "the operations room where the post's routing rules run",
    provenance: { origin: "licensed_stock", usage: "licensed", license: "CC BY-SA 4.0", license_url: "https://creativecommons.org/licenses/by-sa/4.0",
      source_url: "https://commons.wikimedia.org/wiki/File:Server_room.png", title: "File:Server room.png", creator: "Jane Example", credit: "Jane Example via Wikimedia Commons",
      attribution_required: true, attribution: "Image: Jane Example, CC BY-SA 4.0, via Wikimedia Commons", retrieved: "Commons thumbnail (1600px wide)", retrieved_at: day(0), original_sha1: "abc123" },
    media_relevance: { concept: "the real operations floor", visual_type: "photo", relevance_reason: "shows the environment the post is about", copied_post_text_ratio: null,
      factual_claims: [], source_requirements: [], media_decision: "accepted", problems: [], text_checked: false,
      semantic: { subject: "IT operations teams", subject_terms: ["IT operations", "data center"], matched_terms: ["data center"], metadata_checked: true } },
    selection: { source: "Wikimedia Commons API", subject: "IT operations teams", subject_terms: ["IT operations", "data center"], selected: "File:Server room.png",
      tried: [{ title: "File:Ops team.png", outcome: "refused", license: "CC BY-NC 2.0", why: "licence 'CC BY-NC 2.0' does not allow reuse" },
              { title: "File:Server room.png", outcome: "selected", license: "CC BY-SA 4.0", matched_terms: ["data center"] }] } } });
snap.preview_media.push({ post_id: "20261014-demo-e", sha256: "5".repeat(64), bytes: 240000, mime: "image/png" });
snap.version_media = [{ post_id: "20261006-demo-a", version: 1, sha256: "1".repeat(64), bytes: 100, mime: "image/png" }];
const identity = { ok: true, status: "verified", person_urn: "urn:li:person:TestPerson1", configured_person_urn: "urn:li:person:TestPerson1", person_urn_matches: true, api_version: "202609", api_version_valid: true };
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "base64");
const files = { "/": ["index.html", "text/html"], "/app.js": ["app.txt", "text/javascript"], "/lib.js": ["lib.txt", "text/javascript"], "/app.css": ["app.css", "text/css"] };
// LCE-048: what /api/refresh-status reports for the old approved post (driven by the test, step by step)
const REQ = { decision_id: "d-00000000-0000-0000-0000-00000000a001", status: "applied", created_at: new Date(Date.now() - 125000).toISOString(),
  resolved_at: new Date(Date.now() - 60000).toISOString(), result: null };
let progressEvents = [], progressReady = false;
function progressStatus(id) {
  if (id !== "20260929-old-approved") return { decision: null, events: [], mirror: null };
  const post = pipe.posts.find((p) => p.post_id === id);
  return { now: new Date().toISOString(), post_id: id, decision: REQ, events: progressEvents,
    mirror: { generated_at: new Date().toISOString(), post: { state: post.state, refresh_request: post.refresh_request ?? null,
      refresh: post.refresh ?? null } } };
}
let reauthed = false;
let redirectMode = "once";       // "once": Access refuses until the sign-in window ran; "always": the retry fails too
const attempts = [];             // request_ids of every decision POST that arrived (incl. refused ones)
const reports = [];              // client-report bodies
const readBody = (req) => new Promise((r) => { let b = ""; req.on("data", (c) => (b += c)); req.on("end", () => r(b)); });
const srv = http.createServer(async (req, res) => {
  const u = new URL(req.url, "http://x");
  const j = (o, s = 200) => { res.writeHead(s, { "content-type": "application/json" }); res.end(JSON.stringify(o)); };
  if (u.pathname === "/" && u.searchParams.get("reauth") === "1") reauthed = true;     // the sign-in window
  if (files[u.pathname]) { const [f, t] = files[u.pathname]; res.writeHead(200, { "content-type": t, "content-security-policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'" }); return res.end(readFileSync(UI + f)); }
  // LCE-041: an expired Access session — the edge redirects API calls to its login page (another origin)
  if (req.method === "POST" && u.pathname === "/api/decisions") {
    const b = JSON.parse((await readBody(req)) || "{}");
    attempts.push(b.request_id);
    if (redirectMode === "conflict") {
      if (!b.replace_pending) return j({ error: "pending_conflict: your refresh (recorded 2026-10-02T22:26:01+00:00) is still pending for this post; confirm to replace it with skip, or cancel it first" }, 409);
      return j({ decision_id: "d-replaced", status: "pending" }, 201);
    }
    if (redirectMode === "record") {      // LCE-043: recorded; the next snapshot carries it as pending (as D1 does)
      const d = { decision_id: "d-skip-recorded", status: "pending", action: b.action, post_id: b.post_id ?? null, plan_date: b.plan_date ?? null, created_at: new Date().toISOString(), payload: {} };
      (snap.decisions ||= []).unshift(d);
      return j({ decision_id: d.decision_id, action: b.action, post_id: d.post_id, status: "pending" }, 201);
    }
    if (reauthed && redirectMode === "once") { reauthed = false; return j({ decision_id: "d-retried", status: "pending" }, 201); }
    res.writeHead(302, { location: "https://access.example.invalid/login" }); return res.end();
  }
  if (req.method === "POST" && u.pathname === "/api/client-report") { reports.push(JSON.parse(await readBody(req))); return j({ report_id: "r-1" }, 201); }
  if (u.pathname === "/api/whoami") { const t = Math.floor(Date.now() / 1000); return j({ subject: "owner@example.com", human: true, session_issued_at: t - 600, session_expires_at: t - 60, server_now: t }); }
  if (u.pathname.startsWith("/api/refresh-status/")) return j(progressStatus(decodeURIComponent(u.pathname.split("/").pop())));
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
  for (const view of ["overview", "upcoming", "posts", "radar", "post/20261006-demo-a", "post/20261008-demo-b", "post/20260929-old-approved", "post/20261014-demo-e", "test/20261012-demo-c", "history", "system"]) {
    await page.goto(`http://127.0.0.1:${port}/#${view}`);
    await page.waitForTimeout(400);
    const text = await page.locator("main").innerText();
    if (!text.trim() || text.includes("Could not load")) errors.push(`${name} ${view}: empty or failed: ${text.slice(0, 200)}`);
    if (await page.locator("main a img").count()) errors.push(`${name} ${view}: an image is wrapped in a link`);
    if (text.includes("/api/posts/")) errors.push(`${name} ${view}: image URL rendered as text`);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    if (overflow) errors.push(`${name} ${view}: horizontal overflow ` + JSON.stringify(await page.evaluate(() => [...document.querySelectorAll("main *")]
      .filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1).slice(-3)
      .map((e) => `${e.tagName}.${e.className} ${Math.round(e.getBoundingClientRect().right)} ${(e.innerText || "").slice(0, 50)}`))));
    // the post preview must keep a readable width (a squeezed grid column does not overflow, so check it)
    if (view.startsWith("post/")) {
      const w = await page.evaluate(() => document.querySelector(".feed")?.getBoundingClientRect().width ?? 999);
      if (w < Math.min(320, vp.width - 48)) errors.push(`${name} ${view}: post preview squeezed to ${Math.round(w)}px`);
    }
    if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-${view.replace("/", "_")}.png`, fullPage: true });
    if (process.env.OUT && view.startsWith("post/")) await page.screenshot({ path: `${process.env.OUT}/${name}-${view.replace("/", "_")}-viewport.png` });
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
  // LCE-047: an image refused by Access waits for the one sign-in flow (banner) instead of a dead end
  if (!(await page.locator("main").innerText()).includes("Image waiting for sign-in")) errors.push(`${name}: image refused by Access is not shown as waiting for sign-in`);
  if (!(await page.locator("#session-banner").count())) errors.push(`${name}: no sign-in banner after an image was refused by Access`);
  const banner = await page.locator("#session-banner").innerText().catch(() => "");
  if (/0 min/.test(banner)) errors.push(`${name}: session length shown as "0 min"`);
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
  for (const want of ["Refreshed, ready for approval", "Versions", "Previous version: v1", "An older fictional hook", "Current version", "Compare with v1"]) {
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
  if (!dlg.includes("Refresh this post?") || !dlg.includes("rejects the current version") || !dlg.includes("preserved in History")) errors.push(`${name}: refresh dialog wrong: ${dlg.slice(0, 200)}`);
  if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-refresh-dialog.png` });
  // LCE-042: confirming while Access refuses the POST: the action is kept, the sign-in window opens by itself,
  // and the SAME action (same request_id) is sent exactly once more — then "recorded", never ambiguous
  attempts.length = 0; reports.length = 0; redirectMode = "once"; reauthed = false;
  const popupP = page.waitForEvent("popup", { timeout: 5000 }).catch(() => null);
  await page.locator("dialog[open]").getByRole("button", { name: "Reject and refresh", exact: true }).click();
  const popup = await popupP;
  if (!popup) errors.push(`${name}: no sign-in window opened`);
  else await popup.waitForEvent("close", { timeout: 5000 }).catch(() => errors.push(`${name}: sign-in window did not close`));
  await page.waitForTimeout(900);
  const recorded = await page.locator("main").innerText();
  if (!recorded.includes("Refresh requested — recorded")) errors.push(`${name}: no "recorded" outcome after signing in`);
  if (attempts.length !== 2 || attempts[0] !== attempts[1] || !String(attempts[0]).startsWith("req-")) errors.push(`${name}: retry not exactly once with the same request_id ${JSON.stringify(attempts)}`);
  const kinds = reports.map((r) => `${r.kind}:${r.detail.stage ?? r.detail.result ?? ""}`);
  if (!kinds.includes("access_redirect:action") || !kinds.includes("retry_succeeded:ok")) errors.push(`${name}: evidence not reported ${JSON.stringify(kinds)}`);
  const ev = reports.find((r) => r.kind === "access_redirect");
  if (ev && (ev.detail.preflight !== "ok" || typeof ev.detail.ms_after_preflight_ok !== "number" || !ev.detail.last_ok_get_at)) errors.push(`${name}: evidence lacks timing ${JSON.stringify(ev.detail)}`);
  if (JSON.stringify(reports).match(/eyJ|CF_Authorization/)) errors.push(`${name}: a report carries a token`);
  if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-refresh-recorded.png` });
  // and when even the retry is refused: an explicit failure with "Try again", never a silent loss
  attempts.length = 0; redirectMode = "always";
  await page.locator("section.card", { hasText: "Controls" }).getByRole("button", { name: "Skip", exact: true }).click();
  await page.waitForTimeout(200);
  const sdlg = await page.locator("dialog[open]").innerText().catch(() => "");
  if (!sdlg.includes("slot is released") || !sdlg.includes("No replacement is generated")) errors.push(`${name}: skip dialog unclear: ${sdlg.slice(0, 200)}`);
  const pop2 = page.waitForEvent("popup", { timeout: 5000 }).catch(() => null);
  await page.locator("dialog[open]").getByRole("button", { name: "Skip and release the slot" }).click();
  const p2 = await pop2;
  if (p2) await p2.waitForEvent("close", { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(900);
  const fail = await page.locator("main").innerText();
  if (!fail.includes("Skip failed — the request was not recorded") || !(await page.getByRole("button", { name: "Try again" }).count())) errors.push(`${name}: failed action not shown as not recorded`);
  if (attempts.length !== 2) errors.push(`${name}: expected one retry, got ${attempts.length} attempts`);
  if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-skip-not-recorded.png` });
  // a pending Refresh is never replaced silently by another decision: the owner is asked first
  attempts.length = 0; redirectMode = "conflict";
  await page.goto(`http://127.0.0.1:${port}/#post/20261006-demo-a`);
  await page.waitForTimeout(400);
  await page.locator("section.card", { hasText: "Controls" }).getByRole("button", { name: "Skip", exact: true }).click();
  await page.waitForTimeout(200);
  await page.locator("dialog[open]").getByRole("button", { name: "Skip and release the slot" }).click();
  await page.waitForTimeout(600);
  const cdlg = await page.locator("dialog[open]").innerText().catch(() => "");
  if (!cdlg.includes("Replace your pending decision?") || !cdlg.includes("Your refresh")) errors.push(`${name}: no confirmation before replacing a pending refresh (${cdlg.slice(0, 160)})`);
  await page.locator("dialog[open]").getByRole("button", { name: "Cancel" }).click();
  await page.waitForTimeout(300);
  if (!(await page.locator("main").innerText()).includes("Skip not sent — your pending refresh stays")) errors.push(`${name}: keeping the pending refresh not shown`);
  if (attempts.length !== 1) errors.push(`${name}: a replacement was sent without confirmation (${attempts.length})`);
  // LCE-043: a recorded Skip ends the version at once: slot released, no Approve/Refresh/Edit/Reschedule, only Undo
  attempts.length = 0; redirectMode = "record";
  await page.locator("section.card", { hasText: "Controls" }).getByRole("button", { name: "Skip", exact: true }).click();
  await page.waitForTimeout(200);
  await page.locator("dialog[open]").getByRole("button", { name: "Skip and release the slot" }).click();
  await page.waitForTimeout(400);
  if (await page.locator("dialog[open]").count()) await page.locator("dialog[open]").getByRole("button", { name: /Replace|Skip/ }).first().click();
  await page.waitForTimeout(900);
  const ctl2 = await page.locator("section.card", { hasText: "Controls" }).innerText();
  const main2 = await page.locator("main").innerText();
  for (const gone of ["Approve", "Refresh", "Edit", "Reschedule", "Reject"]) {
    if (await page.locator("section.card", { hasText: "Controls" }).getByRole("button", { name: gone, exact: true }).count()) errors.push(`${name}: "${gone}" still offered after a recorded skip (${ctl2})`);
  }
  if (!/skipped, slot released/i.test(main2)) errors.push(`${name}: status after a recorded skip is not "Skipped · slot released"`);
  if (!(await page.getByRole("button", { name: "Undo skip" }).count())) errors.push(`${name}: no Undo skip after a recorded skip`);
  if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-skip-recorded.png`, fullPage: true });
  await page.goto(`http://127.0.0.1:${port}/#upcoming`);
  await page.waitForTimeout(400);
  const skRow = page.locator(".row", { has: page.locator('a[href="#post/20261006-demo-a"]') }).first();
  const rowText = await skRow.innerText();
  if (!/\bskipped\b/i.test(rowText)) errors.push(`${name}: upcoming row not released after skip (${rowText.slice(0, 160)})`);
  for (const gone of ["Approve", "Refresh", "Edit", "Reschedule"]) if (await skRow.getByRole("button", { name: gone, exact: true }).count()) errors.push(`${name}: upcoming row still offers ${gone} after skip`);
  if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-upcoming-after-skip.png`, fullPage: true });
  // LCE-045 (the production case): the page stays open while the private run resolves the skip as refused
  // (the owner withdrew it). Refreshing the state must replace "Skip requested — recorded" with its real fate
  // and give the version its controls back — never both at once.
  const sk = (snap.decisions || []).find((d) => d.decision_id === "d-skip-recorded");
  Object.assign(sk, { status: "refused", result: "overridden by the owner: treat as Refresh", resolved_at: new Date().toISOString() });
  await page.goto(`http://127.0.0.1:${port}/#post/20261006-demo-a`);
  await page.locator("#refresh").click().catch(() => errors.push(`${name}: no ↻ button`));
  await page.waitForTimeout(700);
  const after = await page.locator("main").innerText();
  if (/skip requested — recorded/i.test(after)) errors.push(`${name}: stale "Skip requested — recorded" after the skip was refused`);
  if (!/was not applied: overridden by the owner/i.test(after)) errors.push(`${name}: the refused skip's fate is not shown`);
  if (/skipped · slot released/i.test(after)) errors.push(`${name}: still "Skipped" after the skip was refused`);
  if (!(await page.locator("section.card", { hasText: "Controls" }).getByRole("button", { name: "Approve", exact: true }).count())) errors.push(`${name}: controls not back after the skip was refused`);
  if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-skip-refused-later.png`, fullPage: true });
  snap.decisions = (snap.decisions || []).filter((d) => d.decision_id !== "d-skip-recorded");   // the owner undid it
  await page.reload();
  redirectMode = "once";
  // LCE-048: live Refresh progress on the real stages, then the replacement appears without a reload
  if (name === "desktop") {
    const old = pipe.posts.find((p) => p.post_id === "20260929-old-approved");
    const saved = JSON.parse(JSON.stringify(old));
    old.refresh_request = { requested_at: REQ.created_at, requested_by: "cloud-access:owner@example.com", decision_id: REQ.decision_id, rejected_version: 1 };
    progressEvents = []; progressReady = false;
    await page.goto(`http://127.0.0.1:${port}/#post/20260929-old-approved`);
    await page.locator("#refresh").click();
    await page.waitForTimeout(1200);
    let t = await page.locator("main").innerText();
    if (!/Refresh requested, writing replacement/.test(t) || !/Waiting for writing session/.test(t)) errors.push(`${name}: no live progress panel while waiting: ${t.slice(0, 300)}`);
    if (/actively processing/i.test(t)) errors.push(`${name}: claims processing before any worker started`);
    if (/remaining/i.test(t)) errors.push(`${name}: shows a remaining-time estimate`);
    const e1 = await page.locator("[data-elapsed-since]").innerText();
    await page.waitForTimeout(2100);
    const e2 = await page.locator("[data-elapsed-since]").innerText();
    if (e1 === e2 || !/^Elapsed: \d{2}:\d{2}$/.test(e2)) errors.push(`${name}: elapsed time does not tick from the request (${e1} → ${e2})`);
    progressEvents = [{ stage: "worker_started", at: new Date().toISOString(), note: "manual session", reported_by: "svc" },
                      { stage: "researching", at: new Date().toISOString(), reported_by: "svc" }];
    await page.locator("#refresh").click();
    await page.waitForTimeout(1200);
    t = await page.locator("main").innerText();
    if (!/actively processing/i.test(t)) errors.push(`${name}: started worker not shown as processing`);
    if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-refresh-progress.png`, fullPage: true });
    // the backend finishes: mirror has the new version for THIS request; the page must update by itself
    delete old.refresh_request;
    Object.assign(old, { state: "AWAITING_APPROVAL", text: "A brand new fictional replacement hook.\n\nNew body.", actual_hash: "7".repeat(64),
      refresh: { completed_at: new Date().toISOString(), by: "session", reason: "new angle", outcome: "refreshed", previous_version: 1, decision_id: REQ.decision_id } });
    progressEvents.push({ stage: "replacement_ready", at: new Date().toISOString(), reported_by: "svc" });
    await page.waitForTimeout(17000);   // one poll interval, no click, no reload
    t = await page.locator("main").innerText();
    if (!t.includes("A brand new fictional replacement hook")) errors.push(`${name}: the replacement did not appear automatically`);
    if (!/refreshed, ready for approval/i.test(t)) errors.push(`${name}: not shown as Refreshed · Awaiting approval after it was ready`);
    if (/writing a replacement/i.test(t)) errors.push(`${name}: still shows "writing a replacement" after it was ready`);
    if (process.env.OUT) await page.screenshot({ path: `${process.env.OUT}/${name}-refresh-ready.png`, fullPage: true });
    for (const k of Object.keys(old)) delete old[k];
    Object.assign(old, saved);                       // back to the fixture for the checks below
    progressEvents = [];
    await page.locator("#refresh").click();
    await page.waitForTimeout(800);
  }
  // a scheduled (cloud-queued) post has no Refresh; the requested one says so
  await page.goto(`http://127.0.0.1:${port}/#upcoming`);
  await page.waitForTimeout(400);
  const upr = await page.locator("main").innerText();
  if (!upr.includes("Writing replacement")) errors.push(`${name}: upcoming does not show the requested refresh as "Writing replacement"`);
  const queuedRow = page.locator(".row.status-scheduled").first();
  if (await queuedRow.getByRole("button", { name: "Refresh" }).count()) errors.push(`${name}: a scheduled post offers Refresh`);
  // media relevance in the media card
  await page.goto(`http://127.0.0.1:${port}/#post/20261008-demo-b`);
  await page.waitForTimeout(400);
  const mc = await page.locator("section.card", { hasText: "Visual concept" }).innerText().catch(() => "");
  for (const want of ["Relevant to the post", "rules before models", "flow", "5%", "Image hash"]) if (!mc.includes(want)) errors.push(`${name}: media card lacks "${want}"`);
  // LCE-043: a real image shows its source, licence, creator, attribution and how it was found
  await page.goto(`http://127.0.0.1:${port}/#post/20261014-demo-e`);
  await page.waitForTimeout(400);
  const media = page.locator("section.card", { hasText: "Source file" });
  await media.locator("summary", { hasText: "Image search" }).click().catch(() => errors.push(`${name}: no image search record`));
  const mtext = await media.innerText().catch(() => "");
  for (const want of ["File:Server room.png", "Jane Example", "CC BY-SA 4.0", "creativecommons.org/licenses/by-sa/4.0", "required · in the post", "commons.wikimedia.org/wiki/File:Server_room.png",
    "Commons thumbnail", "data center", "Refused: File:Ops team.png", "does not allow reuse", "Selected: File:Server room.png"]) {
    if (!mtext.includes(want)) errors.push(`${name}: media card lacks "${want}"`);
  }
  if (process.env.OUT) await media.screenshot({ path: `${process.env.OUT}/${name}-real-image-card.png` });
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
const real = errors.filter((e) => !e.includes("404 (Not Found)") && !e.includes("409 (Conflict)"));   // favicon; the expected pending_conflict
console.log(real.length ? "ERRORS:\n" + real.join("\n") : "OK no errors");
process.exit(real.length ? 1 : 0);
