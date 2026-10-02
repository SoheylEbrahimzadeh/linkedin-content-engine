// Read-only LinkedIn identity check (LCE-035). One GET to LinkedIn's OpenID
// Connect userinfo endpoint with the runtime LINKEDIN_TOKEN secret, to prove the
// token works and to derive the member's person URN for publishing.
//
// Facts (docs/PUBLISHING.md lists the sources):
// - GET https://api.linkedin.com/v2/userinfo, `Authorization: Bearer <token>`,
//   scope `openid` (+ `profile` for name/picture). It is the OIDC userinfo
//   endpoint, not a versioned /rest API: no Linkedin-Version header.
// - The member id is the `sub` claim; the Posts API author is urn:li:person:{sub}.
//
// It never publishes or writes anything at LinkedIn, never writes D1, and never
// returns the token or the profile (name, picture, email): only the URN and
// whether it matches the configured settings.

import type { Settings } from "./db";
import type { FetchLike } from "./linkedin";

export const USERINFO_URL = "https://api.linkedin.com/v2/userinfo";
const SUB_RE = /^[A-Za-z0-9_-]{1,128}$/;
const PERSON_URN_RE = /^urn:li:person:[A-Za-z0-9_-]+$/;

export type IdentityStatus = "verified" | "token_missing" | "token_rejected" | "forbidden" |
  "rate_limited" | "malformed_response" | "linkedin_error" | "timeout" | "network_error";

export type IdentityReport = {
  ok: boolean;
  status: IdentityStatus;
  http_status?: number;          // LinkedIn's status, when it answered
  person_urn?: string;           // derived from `sub`; only when verified
  configured_person_urn: string | null;
  person_urn_matches: boolean | null;   // null: nothing configured (or not verified)
  api_version: string | null;
  api_version_valid: boolean;
  reason?: string;
};

// HTTP status of our own answer for each outcome (the body says the rest).
export const IDENTITY_HTTP: Record<IdentityStatus, number> = {
  verified: 200, token_missing: 503, token_rejected: 502, forbidden: 502, rate_limited: 502,
  malformed_response: 502, linkedin_error: 502, timeout: 504, network_error: 502,
};

export async function linkedinIdentity(token: string | undefined, settings: Settings, fetchImpl: FetchLike,
                                       timeoutMs = 10000): Promise<IdentityReport> {
  const configured = settings.person_urn && PERSON_URN_RE.test(settings.person_urn) ? settings.person_urn : null;
  const base = {
    configured_person_urn: settings.person_urn ?? null,
    person_urn_matches: null,
    api_version: settings.api_version ?? null,
    api_version_valid: /^\d{6}$/.test(settings.api_version ?? ""),
  };
  const fail = (status: IdentityStatus, reason: string, http?: number): IdentityReport =>
    ({ ok: false, status, ...(http === undefined ? {} : { http_status: http }), ...base, reason });
  if (!token) return fail("token_missing", "no LINKEDIN_TOKEN secret in the Worker runtime");
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let resp: Response;
  try {
    resp = await fetchImpl(USERINFO_URL, {
      method: "GET", headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      redirect: "manual", signal: ctrl.signal,
    });
  } catch (err) {
    const name = (err as Error)?.name ?? "Error";
    return name === "AbortError" || name === "TimeoutError"
      ? fail("timeout", `LinkedIn did not answer within ${timeoutMs} ms`)
      : fail("network_error", `request failed: ${name}`);
  } finally {
    clearTimeout(timer);
  }
  const http = resp.status;
  if (http === 401) return fail("token_rejected", "LinkedIn rejected the token (invalid, expired or revoked)", http);
  if (http === 403) return fail("forbidden", "token lacks the openid scope (or the app lacks the product)", http);
  if (http === 429) return fail("rate_limited", "LinkedIn rate limit; try later", http);
  if (http !== 200) return fail("linkedin_error", `LinkedIn answered HTTP ${http}`, http);
  let data: unknown;
  try {
    data = JSON.parse(await resp.text());
  } catch {
    return fail("malformed_response", "userinfo body is not JSON", http);
  }
  const sub = data && typeof data === "object" && !Array.isArray(data) ? (data as Record<string, unknown>).sub : undefined;
  if (typeof sub !== "string" || !SUB_RE.test(sub)) {
    return fail("malformed_response", "userinfo has no usable `sub` claim", http);
  }
  const urn = `urn:li:person:${sub}`;
  return { ok: true, status: "verified", http_status: http, person_urn: urn, ...base,
           person_urn_matches: configured === null ? null : configured === urn };
}
