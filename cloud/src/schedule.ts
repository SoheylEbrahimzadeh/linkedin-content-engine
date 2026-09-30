// Port of lce.schedule: timezone/DST-aware slots with the same slot ids.
// Rules (as zoneinfo fold=0): a wall time in a spring-forward gap is shifted
// forward by the gap and marked dst_adjusted; in a fall-back overlap the first
// occurrence is used.

export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const TIME_RE = /^([01]\d|2[0-3]):([0-5]\d)$/;

export type SlotSpec = { day: string; hour: number; minute: number };
export type Schedule = { timezone: string; postsPerWeek: number; slots: SlotSpec[] };
export type Slot = {
  slot_id: string; day: string; time: string; local: string; utc: string;
  timezone: string; dst_adjusted: boolean;
};

export class ScheduleError extends Error {}

export function isoUtc(ms: number): string {
  return new Date(Math.floor(ms / 1000) * 1000).toISOString().replace(".000Z", "+00:00");
}

export function parseIsoUtc(s: string): number {
  if (!/[+-]\d{2}:\d{2}$|Z$/.test(s)) throw new ScheduleError(`timestamp without offset: ${s}`);
  const ms = Date.parse(s);
  if (Number.isNaN(ms)) throw new ScheduleError(`invalid timestamp: ${s}`);
  return ms;
}

const formatters = new Map<string, Intl.DateTimeFormat>();
function formatter(tz: string): Intl.DateTimeFormat {
  let f = formatters.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat("en-US", {
      timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
    formatters.set(tz, f);
  }
  return f;
}

// Local wall time (as a UTC-based millisecond count) of an instant in tz.
export function wallMs(tz: string, instant: number): number {
  const p: Record<string, number> = {};
  for (const part of formatter(tz).formatToParts(new Date(instant))) {
    if (part.type !== "literal") p[part.type] = Number(part.value);
  }
  return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
}

function offsetMs(tz: string, instant: number): number {
  return wallMs(tz, instant) - Math.floor(instant / 1000) * 1000;
}

export function loadSchedule(settings: Record<string, unknown>): Schedule {
  const tz = settings.timezone;
  if (!tz || typeof tz !== "string") throw new ScheduleError("timezone is not configured");
  try {
    formatter(tz);
  } catch {
    throw new ScheduleError(`timezone ${JSON.stringify(tz)} is not a valid IANA timezone`);
  }
  const cadence = settings.cadence as { posts_per_week?: unknown; slots?: unknown } | undefined;
  if (!cadence || typeof cadence !== "object") throw new ScheduleError("cadence is not configured");
  const raw = cadence.slots;
  if (!Array.isArray(raw) || raw.length === 0) throw new ScheduleError("cadence.slots must be a non-empty list");
  const specs: SlotSpec[] = raw.map((s, i) => {
    const day = s?.day, t = s?.time;
    if (!DAYS.includes(day)) throw new ScheduleError(`slot ${i}: day must be one of ${DAYS.join(", ")}`);
    if (typeof t !== "string") throw new ScheduleError(`slot ${i}: time must be a quoted 'HH:MM' string`);
    const m = TIME_RE.exec(t);
    if (!m) throw new ScheduleError(`slot ${i}: time ${JSON.stringify(t)} is not HH:MM (24h)`);
    return { day, hour: Number(m[1]), minute: Number(m[2]) };
  });
  const keys = new Set(specs.map((s) => `${s.day}${s.hour}:${s.minute}`));
  if (keys.size !== specs.length) throw new ScheduleError("cadence.slots contains duplicates");
  if (cadence.posts_per_week !== specs.length) {
    throw new ScheduleError("cadence.posts_per_week must equal the number of slots");
  }
  specs.sort((a, b) => DAYS.indexOf(a.day as never) - DAYS.indexOf(b.day as never) ||
    a.hour - b.hour || a.minute - b.minute);
  return { timezone: tz, postsPerWeek: specs.length, slots: specs };
}

const pad = (n: number, w = 2) => String(n).padStart(w, "0");

export function resolveLocal(y: number, m: number, d: number, hh: number, mm: number, tz: string):
    { utc: number; adjusted: boolean } {
  const wall = Date.UTC(y, m - 1, d, hh, mm);
  const before = offsetMs(tz, wall - 14 * 3600e3);
  const after = offsetMs(tz, wall + 14 * 3600e3);
  const c1 = wall - before;
  if (wallMs(tz, c1) === wall) return { utc: c1, adjusted: false };
  const c2 = wall - after;
  if (wallMs(tz, c2) === wall) return { utc: c2, adjusted: false };
  return { utc: c1, adjusted: true }; // gap: pre-transition offset → shifted forward
}

function localIso(tz: string, utc: number): string {
  const off = Math.round(offsetMs(tz, utc) / 60000);
  const w = new Date(wallMs(tz, utc));
  const sign = off >= 0 ? "+" : "-";
  const a = Math.abs(off);
  return `${w.getUTCFullYear()}-${pad(w.getUTCMonth() + 1)}-${pad(w.getUTCDate())}T` +
    `${pad(w.getUTCHours())}:${pad(w.getUTCMinutes())}:${pad(w.getUTCSeconds())}` +
    `${sign}${pad(Math.floor(a / 60))}:${pad(a % 60)}`;
}

export function slotFor(s: Schedule, spec: SlotSpec, y: number, m: number, d: number): Slot {
  const { utc, adjusted } = resolveLocal(y, m, d, spec.hour, spec.minute, s.timezone);
  const date = `${y}-${pad(m)}-${pad(d)}`;
  return {
    slot_id: `${date}-${spec.day}-${pad(spec.hour)}${pad(spec.minute)}`,
    day: spec.day, time: `${pad(spec.hour)}:${pad(spec.minute)}`,
    local: localIso(s.timezone, utc), utc: isoUtc(utc), timezone: s.timezone, dst_adjusted: adjusted,
  };
}

export function slotsBetween(s: Schedule, startMs: number, endMs: number): Slot[] {
  const first = new Date(wallMs(s.timezone, startMs) - 86400e3);
  const last = new Date(wallMs(s.timezone, endMs) + 86400e3);
  const out: Slot[] = [];
  for (let t = Date.UTC(first.getUTCFullYear(), first.getUTCMonth(), first.getUTCDate());
       t <= last.getTime(); t += 86400e3) {
    const day = new Date(t);
    const name = DAYS[(day.getUTCDay() + 6) % 7];
    for (const spec of s.slots) {
      if (spec.day !== name) continue;
      const slot = slotFor(s, spec, day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate());
      const u = Date.parse(slot.utc);
      if (startMs <= u && u < endMs) out.push(slot);
    }
  }
  return out.sort((a, b) => Date.parse(a.utc) - Date.parse(b.utc));
}

export function slotById(s: Schedule, slotId: string): Slot {
  const m = /^(\d{4})-(\d{2})-(\d{2})-(mon|tue|wed|thu|fri|sat|sun)-(\d{2})(\d{2})$/.exec(slotId);
  if (!m) throw new ScheduleError(`invalid slot id ${slotId}`);
  const [y, mo, d, day, hh, mm] = [Number(m[1]), Number(m[2]), Number(m[3]), m[4], Number(m[5]), Number(m[6])];
  const spec = s.slots.find((x) => x.day === day && x.hour === hh && x.minute === mm);
  if (!spec) throw new ScheduleError(`${slotId} is not a configured slot`);
  const weekday = DAYS[(new Date(Date.UTC(y, mo - 1, d)).getUTCDay() + 6) % 7];
  if (weekday !== day) throw new ScheduleError(`${slotId}: date is not a ${day}`);
  return slotFor(s, spec, y, mo, d);
}
