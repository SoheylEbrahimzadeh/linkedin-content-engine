// D1 helpers. The token is never read from or written to D1.

import { isoUtc } from "./schedule";

export type Env = {
  DB: D1Database;
  LINKEDIN_TOKEN?: string;
  ACCESS_TEAM_DOMAIN?: string;
  ACCESS_AUD?: string;
};

export const SETTING_KEYS = ["auto_publish", "provider", "timezone", "cadence", "api_version",
  "person_urn", "visibility", "token_expires_at", "max_lateness_minutes"] as const;

export type Settings = {
  auto_publish: boolean; provider: string; timezone?: string; cadence?: unknown;
  api_version?: string; person_urn?: string; visibility: string; token_expires_at?: string;
  max_lateness_minutes: number;
};

export async function loadSettings(db: D1Database): Promise<Settings> {
  const rows = (await db.prepare("SELECT key, value FROM settings").all<{ key: string; value: string }>()).results;
  const raw = Object.fromEntries(rows.map((r) => [r.key, r.value]));
  let cadence: unknown;
  try {
    cadence = raw.cadence ? JSON.parse(raw.cadence) : undefined;
  } catch {
    cadence = undefined;
  }
  return {
    auto_publish: raw.auto_publish === "true",
    provider: raw.provider ?? "none",
    timezone: raw.timezone,
    cadence,
    api_version: raw.api_version,
    person_urn: raw.person_urn,
    visibility: raw.visibility ?? "PUBLIC",
    token_expires_at: raw.token_expires_at,
    max_lateness_minutes: Number(raw.max_lateness_minutes ?? 180),
  };
}

export function event(db: D1Database, at: number, name: string, actor: string, postId: string | null,
                      detail: Record<string, unknown> = {}): D1PreparedStatement {
  return db.prepare("INSERT INTO events (at, event, actor, post_id, detail) VALUES (?, ?, ?, ?, ?)")
    .bind(isoUtc(at), name, actor, postId, JSON.stringify(detail));
}

export type PostRow = {
  post_id: string; text: string; language: string; plan_date: string | null; state: string;
  content_hash: string; approved_hash: string; approved_at: string; pushed_at: string; pushed_by: string;
  updated_at: string;
};

export type ConsentRow = {
  consent_id: string; post_id: string; slot_id: string; slot_utc: string; approved_hash: string;
  status: string; created_at: string; created_by: string; resolved_at: string | null; reason: string | null;
};
