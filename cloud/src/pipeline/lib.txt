// Pure helpers for the Web Control Center (no DOM access; unit-tested with node --test).

export const UNKNOWN = "unknown";

export const STATE_META = {
  RESEARCHED: { label: "Researched", tone: "info" },
  SELECTED: { label: "Selected", tone: "info" },
  NEEDS_INPUT: { label: "Needs input", tone: "warn" },
  DRAFTED: { label: "Drafted", tone: "info" },
  HUMANIZED: { label: "Humanized", tone: "info" },
  NEEDS_REVISION: { label: "Needs revision", tone: "warn" },
  QA_PASSED: { label: "QA passed", tone: "info" },
  DUPLICATE_CHECKED: { label: "Duplicate-checked", tone: "info" },
  AWAITING_APPROVAL: { label: "Awaiting approval", tone: "warn" },
  APPROVED: { label: "Approved", tone: "ok" },
  READY_TO_PUBLISH: { label: "Ready to publish", tone: "ok" },
  PUBLISHING: { label: "Publishing (in progress or interrupted)", tone: "warn" },
  PUBLISHED: { label: "Published", tone: "ok" },
  PUBLISH_FAILED: { label: "Publish failed (not created)", tone: "error" },
  REJECTED: { label: "Rejected", tone: "muted" },
  FAILED: { label: "Failed", tone: "error" },
  NEEDS_RECONCILE: { label: "Needs reconcile", tone: "error" },
};

export function stateMeta(state) {
  if (state == null) return { label: UNKNOWN, tone: "muted" };
  return STATE_META[state] || { label: String(state), tone: "muted" };
}

/** Show missing values as "unknown" instead of inventing them. */
export function show(value) {
  if (value === null || value === undefined || value === "") return UNKNOWN;
  if (Array.isArray(value)) return value.length ? value.join(", ") : "none";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return String(value);
}

export function shortHash(hash, n = 12) {
  return typeof hash === "string" && hash.length >= n ? hash.slice(0, n) : UNKNOWN;
}

export function fmtDateTime(iso, timeZone) {
  if (!iso) return UNKNOWN;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  try {
    return new Intl.DateTimeFormat("en-GB", {
      dateStyle: "medium", timeStyle: "short", timeZone: timeZone || undefined,
    }).format(d);
  } catch {
    return d.toISOString();
  }
}

export function publicationLabel(status) {
  return {
    not_published: "Not published",
    ready_to_publish: "Ready (not published)",
    publishing: "Publishing…",
    published: "Published",
    unknown: "Unknown — needs reconciliation",
  }[status] || show(status);
}

/** Posts waiting for a human decision vs. already decided. */
export function splitApprovals(posts) {
  const pending = [], approved = [], other = [];
  for (const p of posts || []) {
    if (p.state === "AWAITING_APPROVAL") pending.push(p);
    else if (p.state === "APPROVED" || p.state === "READY_TO_PUBLISH") approved.push(p);
    else other.push(p);
  }
  return { pending, approved, other };
}

export function issueCounts(issues) {
  const counts = { FAILED: 0, NEEDS_RECONCILE: 0, INCONSISTENT: 0 };
  for (const i of issues || []) counts[i.kind] = (counts[i.kind] || 0) + 1;
  return counts;
}

/** Mode must match between page config and data; never mix demo and real data. */
export function checkMode(configMode, snapshot) {
  const dataMode = snapshot && snapshot.meta ? snapshot.meta.mode : null;
  if (configMode !== "real" && configMode !== "demo") {
    return { ok: false, message: "Dashboard mode is not configured." };
  }
  if (dataMode !== configMode) {
    return { ok: false, message: `Mode mismatch: page is '${configMode}', data is '${dataMode}'.` };
  }
  return { ok: true, message: configMode === "demo" ? "DEMO MODE — NO PRIVATE DATA" : "REAL DATA" };
}

/** Month grids (Monday first) for calendar entries; only months that have entries. */
export function calendarMonths(entries) {
  const byMonth = new Map();
  for (const e of entries || []) {
    if (!e || typeof e.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(e.date)) continue;
    const key = e.date.slice(0, 7);
    if (!byMonth.has(key)) byMonth.set(key, []);
    byMonth.get(key).push(e);
  }
  const months = [];
  for (const key of [...byMonth.keys()].sort()) {
    const [y, m] = key.split("-").map(Number);
    const first = new Date(Date.UTC(y, m - 1, 1));
    const days = new Date(Date.UTC(y, m, 0)).getUTCDate();
    const offset = (first.getUTCDay() + 6) % 7; // Monday = 0
    const cells = [];
    for (let i = 0; i < offset; i++) cells.push(null);
    for (let d = 1; d <= days; d++) {
      const date = `${key}-${String(d).padStart(2, "0")}`;
      cells.push({ day: d, date, entries: byMonth.get(key).filter((e) => e.date === date) });
    }
    while (cells.length % 7) cells.push(null);
    const weeks = [];
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
    const label = first.toLocaleString("en-GB", { month: "long", year: "numeric", timeZone: "UTC" });
    months.push({ key, label, weeks });
  }
  return months;
}

export function describeRun(ev) {
  const e = ev || {};
  switch (e.event) {
    case "state": return { title: `State → ${show(e.state)}`, detail: e.note || "", tone: stateMeta(e.state).tone };
    case "qa": return { title: `QA ${show(e.status)}`, detail: `${show(e.errors)} errors, ${show(e.warnings)} warnings`, tone: e.status === "passed" ? "ok" : "warn" };
    case "duplicate": return { title: `Duplicate check ${show(e.status)}`, detail: `exact ${show(e.exact)}, near ${show(e.near)}, similar ${show(e.similar)}`, tone: e.status === "passed" ? "ok" : "warn" };
    case "approval": return { title: `Approval: ${show(e.decision)}`, detail: e.content_hash ? `hash ${shortHash(e.content_hash)}` : "", tone: e.decision === "approved" ? "ok" : "muted" };
    case "research.add": return { title: "Research candidate added", detail: `${show(e.candidate_id)} (${show(e.origin)})`, tone: "info" };
    case "research.fetch": return { title: "Feed fetch", detail: `${show(e.added)} added, ${show(e.errors)} errors`, tone: e.errors ? "warn" : "info" };
    case "story.save": return { title: "Story saved", detail: `${show(e.story_id)} (${show(e.status)})`, tone: "info" };
    case "history.import": return { title: "Past post imported", detail: show(e.name), tone: "info" };
    case "scheduler.start": return { title: "Scheduler pass started", detail: `${show(e.planned)} planned action(s)`, tone: "info" };
    case "scheduler.finish": return { title: "Scheduler pass finished", detail: `${show(e.results)} job result(s)`, tone: "info" };
    case "scheduler.locked": return { title: "Scheduler pass skipped (locked)", detail: `held by ${show(e.holder)}`, tone: "warn" };
    case "scheduler.lock_takeover": return { title: "Stale scheduler lock taken over", detail: `previous ${show(e.previous)}`, tone: "warn" };
    case "scheduler.config_invalid": return { title: "Scheduler config invalid", detail: show(e.message), tone: "error" };
    case "job.created": return { title: `Job created: ${show(e.job_id)}`, detail: `slot ${show(e.slot_id)}`, tone: "info" };
    case "job.transition": return { title: `Job ${show(e.job_id)}: ${show(e.from)} → ${show(e.to)}`, detail: show(e.reason), tone: jobStateMeta(e.to).tone };
    case "job.step": return { title: `Job step ${show(e.step)} ${show(e.status)}`, detail: e.detail || "", tone: e.status === "failed" ? "error" : "info" };
    case "job.error": return { title: `Job error: ${show(e.kind)}`, detail: `${e.retryable ? "retryable" : "not retryable"} — ${show(e.message)}`, tone: "error" };
    case "publish.intent": return { title: `Publish attempt ${show(e.attempt)} started`, detail: "intent recorded before sending", tone: "info" };
    case "publish.published": return { title: "Published on LinkedIn", detail: show(e.remote_id), tone: "ok" };
    case "publish.failed": return { title: "Publish failed (not created)", detail: `${show(e.reason)}${e.http_status ? `, HTTP ${e.http_status}` : ""}`, tone: "error" };
    case "publish.ambiguous": return { title: "Publish outcome unknown", detail: `${show(e.reason)} — needs reconciliation`, tone: "error" };
    case "publish.reconciled": return { title: "Publish reconciled by owner", detail: show(e.decision), tone: "info" };
    case "job.linked": return { title: `Job ${show(e.job_id)} linked to post`, detail: show(e.post_id), tone: "info" };
    default: return { title: show(e.event), detail: "", tone: "muted" };
  }
}

/** Status of a stage in the latest run. */
export const RUN_STATUS = {
  done: { symbol: "✓", label: "done", tone: "ok" },
  waiting: { symbol: "…", label: "waiting for human", tone: "warn" },
  failed: { symbol: "✗", label: "failed", tone: "error" },
  running: { symbol: "…", label: "in progress / interrupted", tone: "warn" },
  needs_reconcile: { symbol: "?", label: "outcome unknown — reconcile", tone: "error" },
  rejected: { symbol: "✗", label: "rejected", tone: "muted" },
  needs_input: { symbol: "!", label: "needs input", tone: "warn" },
  not_reached: { symbol: "○", label: "not reached", tone: "muted" },
  not_implemented: { symbol: "—", label: "not implemented", tone: "muted" },
};

export function runStatus(status) {
  return RUN_STATUS[status] || { symbol: "?", label: show(status), tone: "muted" };
}

/** Selected vs. not selected, straight from candidate status (no inferred reasons). */
export function researchGroups(research) {
  const selected = [], unselected = [];
  for (const c of research || []) (c.selected || c.status === "selected" ? selected : unselected).push(c);
  return { selected, unselected };
}

export function claimLabel(n) {
  if (typeof n !== "number") return UNKNOWN;
  return n === 0 ? "no claims extracted" : `${n} claim${n === 1 ? "" : "s"} extracted`;
}

/** Evidence limitation of a duplicate check; null when there is nothing to qualify. */
export function duplicateEvidenceNote(report) {
  if (!report || typeof report.compared_against !== "number") return null;
  if (report.compared_against > 0) return null;
  return report.status === "passed"
    ? "Duplicate check passed — no previous posts were available for comparison (compared against 0 posts). This is not evidence that the post is original."
    : "No previous posts were available for comparison (compared against 0 posts).";
}

export const JOB_STATE_META = {
  SCHEDULED: { label: "Scheduled", tone: "muted" },
  READY: { label: "Ready", tone: "info" },
  RUNNING: { label: "Running", tone: "info" },
  BLOCKED: { label: "Blocked", tone: "warn" },
  SUCCEEDED: { label: "Succeeded", tone: "ok" },
  FAILED: { label: "Failed", tone: "error" },
  SKIPPED: { label: "Skipped", tone: "muted" },
  NEEDS_RECONCILE: { label: "Needs reconcile", tone: "error" },
};

export const BLOCK_REASON_LABEL = {
  awaiting_agent: "waiting for Claude Code (research / draft / humanize)",
  awaiting_revision: "waiting for a revision (QA or duplicate check failed)",
  needs_input: "waiting for owner input",
};

export function jobStateMeta(state, blockedReason) {
  const m = JOB_STATE_META[state] || { label: show(state), tone: "muted" };
  if (state === "BLOCKED" && blockedReason) return { ...m, detail: BLOCK_REASON_LABEL[blockedReason] || blockedReason };
  return m;
}

/** Counts per job state; missing states are 0, never estimated. */
export function jobCounts(counts) {
  const out = {};
  for (const k of Object.keys(JOB_STATE_META)) out[k] = (counts && counts[k]) || 0;
  return out;
}

export const ROUTES = [
  ["dashboard", "Dashboard"], ["brand", "Brand"], ["posts", "Posts"], ["research", "Research"],
  ["calendar", "Calendar"], ["approval", "Approval"], ["publishing", "Publishing"],
  ["automation", "Automation"], ["monitoring", "Monitoring"], ["errors", "Errors / Reconciliation"],
  ["analytics", "Analytics"], ["settings", "Settings"],
];

export function parseRoute(hash) {
  const parts = String(hash || "").replace(/^#\/?/, "").split("/").filter(Boolean);
  const name = ROUTES.some(([r]) => r === parts[0]) ? parts[0] : "dashboard";
  return { name, id: parts[1] ? decodeURIComponent(parts[1]) : null };
}
