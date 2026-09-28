// Web Control Center. Read-only: renders the snapshot, never changes data.
// All text goes through textContent; no HTML from data is ever interpreted.
import {
  ROUTES, calendarMonths, checkMode, claimLabel, describeRun, fmtDateTime, issueCounts, parseRoute,
  duplicateEvidenceNote, publicationLabel, researchGroups, runStatus, shortHash, show, splitApprovals, stateMeta,
} from "./lib.js";

const cfg = window.LCE_CONFIG || {};
let snapshot = null;

// ── tiny DOM builder ────────────────────────────────────────────────
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const badge = (text, tone) => h("span", { class: `badge ${tone || "muted"}` }, text);
const stateBadge = (s) => { const m = stateMeta(s); return badge(m.label, m.tone); };
const card = (title, ...body) => h("section", { class: "card" }, title ? h("h3", {}, title) : null, ...body);
const kv = (rows) => h("dl", { class: "kv" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v instanceof Node ? v : show(v))]));
const mono = (t) => h("code", { class: "mono" }, t);
const link = (href, text) => h("a", { href }, text);
const extLink = (url) => h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, url);
const table = (headers, rows) => h("div", { class: "table-wrap" },
  h("table", {}, h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.length ? rows : h("tr", {}, h("td", { colspan: headers.length, class: "empty" }, "Nothing here yet.")))));
const empty = (text) => h("p", { class: "empty" }, text);
const tz = () => snapshot?.settings?.timezone || undefined;
const when = (iso) => fmtDateTime(iso, tz());

// ── views ───────────────────────────────────────────────────────────
function viewDashboard(s) {
  const m = s.meta, hl = s.health, counts = issueCounts(s.issues);
  const git = m.data.git;
  const upcoming = s.calendar.filter((e) => e.date >= new Date().toISOString().slice(0, 10));
  return [
    h("div", { class: "grid" },
      card("System health", kv([
        ["Mode", badge(m.mode === "demo" ? "DEMO" : "REAL DATA", m.mode === "demo" ? "warn" : "ok")],
        ["Engine", `${m.engine.version} @ ${shortHash(m.engine.commit)}`],
        ["Engine working tree", m.engine.dirty_files == null ? "unknown" : (m.engine.dirty_files ? `${m.engine.dirty_files} uncommitted` : "clean")],
        ["Data source", m.data.label],
        ["Data repo", git.available ? `${show(git.branch)} @ ${shortHash(git.head, 7)}` : "not a git repository / not shown"],
        ["Data working tree", git.dirty_files == null ? "unknown" : (git.dirty_files ? `${git.dirty_files} uncommitted` : "clean")],
        ["Validation", badge(`${hl.validation.status} (${hl.validation.files} files)`, hl.validation.status === "passed" ? "ok" : "error")],
        ["Last run", hl.last_run ? `${when(hl.last_run.at)} — ${describeRun(hl.last_run).title}` : "no runs yet"],
        ["Snapshot generated", when(m.generated_at)],
      ])),
      card("Readiness", kv([
        ["Interview", `${hl.interview.answered}/${hl.interview.total} answered`],
        ["Required missing", hl.interview.required_missing.length ? hl.interview.required_missing.join(", ") : badge("none — ready for drafting", "ok")],
        ["Story bank", `${hl.stories.total} stories (${hl.stories.public} PUBLIC)`],
        ["Open issues", counts.FAILED + counts.NEEDS_RECONCILE + counts.INCONSISTENT ? link("#/errors", `${counts.FAILED} failed, ${counts.NEEDS_RECONCILE} reconcile, ${counts.INCONSISTENT} inconsistent`) : badge("none", "ok")],
        ["Publishing", badge("not implemented", "muted")],
      ])),
    ),
    card("Where posts are now", h("p", { class: "note" }, "Current state of every post (one post counts once)."),
      h("ol", { class: "pipeline" }, s.state_distribution.map((b) =>
        h("li", { class: `stage ${b.implemented ? (b.count ? "has-posts" : "empty-bucket") : "not_implemented"}` },
          h("span", { class: "stage-name" }, b.label),
          h("span", { class: "stage-count big-count" }, String(b.count)),
          b.implemented ? null : h("span", { class: "stage-status" }, "not implemented"))))),
    latestRunCard(s.latest_run),
    card("Current posts", postsTable(s.posts)),
    card("Upcoming calendar", table(["Date", "Topic", "Status", "Approval", "Publication"],
      upcoming.map((e) => h("tr", {}, h("td", {}, e.date), h("td", {}, e.draft_ref ? link(`#/posts/${encodeURIComponent(e.draft_ref)}`, show(e.topic)) : show(e.topic)),
        h("td", {}, e.post_state ? stateBadge(e.post_state) : badge(show(e.status), "muted")), h("td", {}, show(e.approval_status)), h("td", {}, publicationLabel(e.publication_status)))))),
  ];
}

function latestRunCard(run) {
  if (!run) return card("Latest pipeline run", empty("No post has run through the pipeline yet."));
  return card("Latest pipeline run",
    h("p", { class: "note" }, "Stages the most recently active post actually passed through, from its recorded state history.",
      run.text_versions > 1 ? ` The text was revised; QA, duplicate check and approval count only for the current version (${run.text_versions} versions).` : ""),
    kv([["Post", link(`#/posts/${encodeURIComponent(run.post_id)}`, show(run.topic))], ["Current state", stateBadge(run.state)], ["Last activity", when(run.last_activity)]]),
    h("ol", { class: "run-steps" }, run.stages.map((st) => {
      const m = runStatus(st.status);
      return h("li", { class: `step ${st.status}` },
        h("span", { class: `step-mark ${m.tone}`, "aria-hidden": "true" }, m.symbol),
        h("span", { class: "step-name" }, st.label),
        h("span", { class: "step-status" }, m.label, st.at ? ` · ${when(st.at)}` : ""));
    })));
}

function postsTable(posts) {
  return table(["Date", "Topic", "Pillar", "State", "Approval", "Publication", "QA", "Duplicates", "Hash"],
    posts.map((p) => h("tr", {},
      h("td", {}, show(p.plan_date)),
      h("td", {}, link(`#/posts/${encodeURIComponent(p.post_id)}`, show(p.topic))),
      h("td", {}, show(p.pillar_name || p.pillar)),
      h("td", {}, stateBadge(p.state)),
      h("td", {}, show(p.approval.state)),
      h("td", {}, publicationLabel(p.publication_status)),
      h("td", {}, p.qa ? badge(p.qa.status, p.qa.status === "passed" ? "ok" : "warn") : "—"),
      h("td", {}, p.duplicate ? badge(p.duplicate.status, p.duplicate.status === "passed" ? "ok" : "warn") : "—"),
      h("td", {}, mono(shortHash(p.content_hash))))));
}

function viewPosts(s) {
  return [card(`Posts (${s.posts.length})`, postsTable(s.posts))];
}

function findings(report) {
  if (!report) return empty("No report.");
  const all = [...(report.errors || []), ...(report.warnings || [])];
  return all.length ? h("ul", { class: "findings" }, all.map((f) => h("li", {}, badge(f.severity, f.severity === "error" ? "error" : "warn"), " ", mono(f.code), " ", f.message))) : empty("No findings.");
}

function viewPost(s, id) {
  const p = s.posts.find((x) => x.post_id === id);
  if (!p) return [card("Post not found", empty(`No post with id ${id}.`))];
  const hashOk = p.content_hash && p.content_hash === p.actual_hash;
  const dup = p.duplicate_report;
  return [
    h("p", {}, link("#/posts", "← All posts")),
    card(show(p.topic), kv([
      ["Post ID", mono(p.post_id)], ["Planned date", p.plan_date], ["Pillar", p.pillar_name || p.pillar],
      ["Format", p.format], ["Angle", p.angle], ["Language", p.language], ["State", stateBadge(p.state)],
      ["Approval", show(p.approval.state)], ["Approved at", p.approval.approved_at ? when(p.approval.approved_at) : null],
      ["Publication", badge(publicationLabel(p.publication_status), "muted")],
      ["Content hash", mono(show(p.content_hash))],
      ["Hash check", badge(hashOk ? "post.md matches recorded hash" : "MISMATCH or unknown", hashOk ? "ok" : "error")],
    ])),
    h("div", { class: "grid" },
      card("Final post (post.md)", p.text ? h("pre", { class: "post" }, p.text) : empty("No candidate text yet.")),
      card("Draft (draft.md)", p.draft ? h("pre", { class: "post muted" }, p.draft) : empty("No draft."))),
    card("Sources & claims",
      p.sources.length ? h("ul", {}, p.sources.map((x) => h("li", {}, show(x.publisher || x.title), " — ", extLink(x.url)))) : empty("No sources."),
      p.claims.length ? h("ul", { class: "claims" }, p.claims.map((c) => h("li", {}, `“${c.text}”`, " ", h("small", {}, extLink(c.source_url))))) : empty("No recorded claims."),
      kv([["Stories used", p.stories_used], ["Candidate", p.candidate_id]])),
    h("div", { class: "grid" },
      card(`QA — ${show(p.qa && p.qa.status)}`, findings(p.qa_report)),
      card(`Duplicate check — ${show(p.duplicate && p.duplicate.status)}`,
        duplicateEvidenceNote(dup) ? h("p", { class: "evidence-note" }, duplicateEvidenceNote(dup)) : null,
        dup ? kv([
        ["Compared against", `${dup.compared_against} post(s)`], ["Exact", dup.exact], ["Near", dup.near.map((x) => x.ref)],
        ["Similar", dup.similar.map((x) => x.ref)], ["Story reuse", dup.story_reuse.map((x) => x.story)],
        ["Angle reuse", dup.angle_reuse], ["Topic reuse", dup.topic_reuse.map((x) => x.ref)],
      ]) : empty("Not run."))),
    h("div", { class: "grid" },
      card("State history", h("ol", { class: "timeline" }, p.history.map((x) => h("li", {}, stateBadge(x.state), " ", h("time", {}, when(x.at)), x.note ? h("small", {}, ` — ${x.note}`) : null)))),
      card("Approval history", p.approval_events.length ? h("ol", { class: "timeline" }, p.approval_events.map((e) => h("li", {}, badge(show(e.decision), e.decision === "approved" ? "ok" : "muted"), " ", h("time", {}, when(e.at)), e.content_hash ? [" ", mono(shortHash(e.content_hash))] : null))) : empty("No approval decisions recorded."))),
  ];
}

function researchTable(list) {
  return table(["Selection", "Candidate", "Title", "Origin", "Trust", "Claims", "Sources", "Used by"], list.map((c) => h("tr", {},
    h("td", {}, c.selected ? badge("SELECTED", "ok") : badge("NOT SELECTED", "muted"), h("div", { class: "sub" }, `status: ${show(c.status)}`)),
    h("td", {}, mono(c.candidate_id)), h("td", {}, show(c.title)), h("td", {}, show(c.origin)),
    h("td", {}, c.untrusted ? badge("untrusted: true", "warn") : badge(`untrusted: ${show(c.untrusted)}`, "muted")),
    h("td", {}, badge(claimLabel(c.claims_count), c.claims_count ? "info" : "muted"),
      c.claims_count ? h("ul", { class: "claims" }, c.claims.map((x) => h("li", {}, `“${x.text}”`))) : null),
    h("td", {}, (c.sources || []).length ? (c.sources || []).map((x) => h("div", {}, extLink(x.url))) : "none"),
    h("td", {}, (c.used_by_posts || []).length ? c.used_by_posts.map((pid) => h("div", {}, link(`#/posts/${encodeURIComponent(pid)}`, pid))) : "—"))));
}

function viewResearch(s) {
  const { selected, unselected } = researchGroups(s.research);
  const withClaims = s.research.filter((c) => c.claims_count > 0).length;
  return [
    h("div", { class: "grid three" },
      card("Selected", h("p", { class: "big" }, badge(String(selected.length), selected.length ? "ok" : "muted"))),
      card("Not selected", h("p", { class: "big" }, badge(String(unselected.length), "muted"))),
      card("With extracted claims", h("p", { class: "big" }, badge(`${withClaims} / ${s.research.length}`, "info")))),
    h("p", { class: "note" }, "Web content is stored as untrusted data. Claims are copied from sources; their trust level is never upgraded automatically. \"Not selected\" only means no post was created from the candidate; no reason is recorded."),
    card(`Selected (${selected.length})`, researchTable(selected)),
    card(`Not selected (${unselected.length})`, researchTable(unselected)),
  ];
}

function viewCalendar(s) {
  const months = calendarMonths(s.calendar);
  const dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const slots = s.settings.cadence?.slots || [];
  return [
    card("Cadence", slots.length ? h("p", {}, `${s.settings.cadence.posts_per_week} posts/week: `, slots.map((x) => `${x.day} ${x.time}`).join(", "), ` (${show(s.settings.timezone)})`) : empty("Cadence not configured.")),
    ...months.map((mo) => card(mo.label, h("div", { class: "month" },
      dow.map((d) => h("div", { class: "dow" }, d)),
      mo.weeks.flat().map((cell) => cell ? h("div", { class: `day${cell.entries.length ? " has" : ""}` },
        h("span", { class: "num" }, cell.day),
        cell.entries.map((e) => h("a", { class: "entry", href: e.draft_ref ? `#/posts/${encodeURIComponent(e.draft_ref)}` : "#/calendar" },
          e.post_state ? stateBadge(e.post_state) : badge(show(e.status), "muted"), h("span", {}, show(e.topic))))) : h("div", { class: "day blank" }))))),
    months.length ? null : card("Calendar", empty("No calendar entries.")),
    card("List", table(["Date", "Topic", "Pillar", "Format", "Status", "Duplicate check", "Approval", "Publication"],
      s.calendar.map((e) => h("tr", {}, h("td", {}, show(e.date)), h("td", {}, show(e.topic)), h("td", {}, show(e.pillar)), h("td", {}, show(e.format)),
        h("td", {}, e.post_state ? stateBadge(e.post_state) : show(e.status)), h("td", {}, show(e.duplicate_check)), h("td", {}, show(e.approval_status)), h("td", {}, publicationLabel(e.publication_status)))))),
  ];
}

function approvalCard(p, pending) {
  return card(show(p.topic), kv([
    ["Post", link(`#/posts/${encodeURIComponent(p.post_id)}`, p.post_id)], ["State", stateBadge(p.state)],
    ["Content hash", mono(show(p.content_hash))], ["Approval status", show(p.approval.state)],
    ["Approved hash", p.approval.approved_hash ? mono(p.approval.approved_hash) : null],
    ["Hash matches text", p.approval.approved_hash ? badge(p.approval.approved_hash === p.actual_hash ? "yes" : "NO", p.approval.approved_hash === p.actual_hash ? "ok" : "error") : "—"],
    ["Approved at", p.approval.approved_at ? when(p.approval.approved_at) : null],
    ["Approved by", p.approval.approved_by],
    ["Approval events", p.approval_events.length ? p.approval_events.map((e) => `${e.decision} ${when(e.at)}`).join("; ") : "none"],
    ["Publication allowed now", badge("no — no publisher in this phase", "muted")],
  ]), pending ? h("div", { class: "cmd" }, h("p", {}, "Approve or reject in your own terminal (interactive, hash-bound):"),
    h("pre", {}, `lce approve ${p.post_id} --hash ${shortHash(p.content_hash)}\nlce reject ${p.post_id} --reason "..."`)) : null);
}

function viewApproval(s) {
  const { pending, approved } = splitApprovals(s.posts);
  return [
    h("p", { class: "note" }, "This dashboard is read-only and cannot approve. Approval stays in the CLI: interactive terminal, exact content hash, typed confirmation."),
    h("h2", {}, `Awaiting approval (${pending.length})`),
    pending.length ? pending.map((p) => approvalCard(p, true)) : card(null, empty("Nothing is waiting for approval.")),
    h("h2", {}, `Approved (${approved.length})`),
    approved.length ? approved.map((p) => approvalCard(p, false)) : card(null, empty("No approved posts.")),
  ];
}

function viewPublishing(s) {
  const pub = s.publishing;
  return [
    card("Publishing status", kv([
      ["Publishing provider", badge(pub.provider_configured ? "configured" : "NOT CONFIGURED", "muted")],
      ["Provider setting", pub.provider],
      ["LinkedIn access", badge("NOT CONFIGURED", "muted")],
      ["Publishing capability", badge("NOT IMPLEMENTED", "muted")],
    ]), h("p", { class: "note" }, "LinkedIn integration is a future phase. No credentials are requested or stored, and nothing is ever marked as published here.")),
    card("Posts", table(["Post", "State", "Publication status"], pub.posts.map((p) => h("tr", {},
      h("td", {}, link(`#/posts/${encodeURIComponent(p.post_id)}`, p.post_id)), h("td", {}, stateBadge(p.state)),
      h("td", {}, badge(p.publication_status.toUpperCase(), "muted")))))),
  ];
}

function viewMonitoring(s) {
  const runs = [...s.runs].reverse();
  return [card(`Run history (${runs.length} events)`, runs.length ? h("ol", { class: "timeline runs" }, runs.map((e) => {
    const d = describeRun(e);
    return h("li", { class: `run ${d.tone}` }, h("time", {}, when(e.at)), " ", badge(e.event || "event", d.tone), " ",
      h("strong", {}, d.title), e.post_id ? [" ", link(`#/posts/${encodeURIComponent(e.post_id)}`, e.post_id)] : null,
      d.detail ? h("small", {}, ` — ${d.detail}`) : null,
      e.codes && e.codes.length ? h("div", { class: "codes" }, e.codes.map((c) => mono(c))) : null);
  })) : empty("No runs recorded."))];
}

function viewErrors(s) {
  const counts = issueCounts(s.issues);
  const groups = [["FAILED", "Failed", "error"], ["NEEDS_RECONCILE", "Needs reconciliation", "error"], ["INCONSISTENT", "Inconsistent state", "warn"]];
  return [
    h("div", { class: "grid three" }, groups.map(([k, label, tone]) => card(label, h("p", { class: "big" }, badge(String(counts[k] || 0), counts[k] ? tone : "ok"))))),
    s.issues.length ? groups.map(([k, label]) => {
      const list = s.issues.filter((i) => i.kind === k);
      return list.length ? card(label, h("ul", { class: "findings" }, list.map((i) => h("li", {}, badge(i.severity, i.severity === "error" ? "error" : "warn"), " ", i.message, i.post_id ? [" — ", link(`#/posts/${encodeURIComponent(i.post_id)}`, i.post_id)] : null)))) : null;
    }) : card(null, h("p", { class: "ok-msg" }, "No reconciliation issues detected")),
    h("p", { class: "note" }, "FAILED = a run or post failed. NEEDS_RECONCILE = stored state and files disagree and need a decision. INCONSISTENT = metadata out of sync. Normal pipeline states are not issues."),
  ];
}

function viewAnalytics() {
  return [card("Analytics", empty("Not available. Real analytics require published posts, and publishing is not implemented yet. Nothing is estimated or simulated."))];
}

function viewSettings(s) {
  const st = s.settings, v = st.voice;
  return [
    h("p", { class: "note" }, "Safe, allowlisted configuration only. Credentials are never read or shown."),
    h("div", { class: "grid" },
      card("Schedule", kv([["Timezone", st.timezone], ["Posts per week", st.cadence?.posts_per_week],
        ["Slots", (st.cadence?.slots || []).map((x) => `${x.day} ${x.time}`)], ["Approval mode", st.approval_mode],
        ["Publisher", st.publisher_provider], ["LLM runtime", st.llm_runtime]])),
      card("Voice", kv([["Language", v.language], ["Formality", v.formality], ["Tone", v.tone],
        ["Max emojis", v.emoji_max], ["Max hashtags", v.hashtag_max], ["Hashtag placement", v.hashtag_placement],
        ["Max characters", v.max_chars], ["Bullets allowed", v.bullets_allowed], ["Avoided phrases", v.avoid_phrases],
        ["CTA allowed", v.cta_allowed]])),
      card("Topics", kv([["Public topics", st.topics_public], ["Pillars", (st.pillars || []).map((p) => p.name)],
        ["Primary language", st.languages?.primary], ["Planned languages", st.languages?.planned]])),
      card("Research", kv([["Method", st.research.method], ["Feeds", st.research.feeds.map((f) => f.name)]])),
    ),
  ];
}

// ── shell ───────────────────────────────────────────────────────────
function render() {
  const main = document.getElementById("main");
  const route = parseRoute(location.hash);
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.route === route.name));
  document.getElementById("nav").classList.remove("open");
  if (!snapshot) return;
  const views = { dashboard: viewDashboard, posts: viewPosts, research: viewResearch, calendar: viewCalendar,
    approval: viewApproval, publishing: viewPublishing, monitoring: viewMonitoring, errors: viewErrors,
    analytics: viewAnalytics, settings: viewSettings };
  const content = route.name === "posts" && route.id ? viewPost(snapshot, route.id) : views[route.name](snapshot);
  const title = ROUTES.find(([r]) => r === route.name)[1];
  main.replaceChildren(h("h1", {}, title), ...content.flat().filter(Boolean));
  main.focus({ preventScroll: true });
}

function setBanner(text, tone) {
  const b = document.getElementById("mode-banner");
  b.textContent = text;
  b.className = `mode-banner ${tone}`;
}

async function load() {
  const main = document.getElementById("main");
  try {
    const res = await fetch(cfg.snapshotUrl, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const mode = checkMode(cfg.mode, data);
    if (!mode.ok) throw new Error(mode.message);
    snapshot = data;
    setBanner(mode.message, data.meta.mode === "demo" ? "demo" : "real");
    document.getElementById("refresh").hidden = data.meta.mode === "demo";
    render();
  } catch (err) {
    snapshot = null;
    setBanner("NO DATA LOADED", "error");
    main.replaceChildren(h("h1", {}, "Could not load data"), card(null, h("p", {}, String(err.message || err)),
      h("p", { class: "note" }, "The dashboard never substitutes other data. Check that `lce dashboard serve` is running against your data directory.")));
  }
}

function init() {
  const nav = document.getElementById("nav");
  nav.replaceChildren(...ROUTES.map(([r, label]) => h("a", { href: `#/${r}`, "data-route": r }, label)));
  document.getElementById("menu").addEventListener("click", () => nav.classList.toggle("open"));
  document.getElementById("refresh").addEventListener("click", load);
  window.addEventListener("hashchange", render);
  load();
}

init();
