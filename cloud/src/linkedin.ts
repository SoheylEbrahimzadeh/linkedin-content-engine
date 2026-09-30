// Port of lce.publish.linkedin (official Posts API). Same request shape and the
// same outcome mapping, enforced by shared test vectors. One difference is
// deliberate: a Worker cannot tell whether a failed fetch reached LinkedIn, so
// every transport error is AMBIGUOUS (never "not sent").

import { stripText, toLittle } from "./text";

export const POSTS_URL = "https://api.linkedin.com/rest/posts";
const POST_URN_RE = /^urn:li:(share|ugcPost):\d+$/;
const PERSON_URN_RE = /^urn:li:person:[A-Za-z0-9_-]+$/;
const REJECTED = new Set([400, 401, 403, 404, 422]);

export type LinkedInConfig = { apiVersion: string; personUrn: string; visibility: string; maxChars: number };
export type Outcome = "published" | "rejected" | "ambiguous";
export type PublishResult = {
  outcome: Outcome; remoteId?: string; url?: string;
  detail: { http_status?: number; reason?: string; message?: string; retryable?: boolean; sent: boolean };
};
export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export function configProblems(c: LinkedInConfig): string[] {
  const out: string[] = [];
  if (!/^\d{6}$/.test(c.apiVersion ?? "")) out.push("api_version must be YYYYMM");
  if (!PERSON_URN_RE.test(c.personUrn ?? "")) out.push("person_urn must look like urn:li:person:<id>");
  if (!["PUBLIC", "CONNECTIONS", "LOGGED_IN"].includes(c.visibility)) out.push("invalid visibility");
  return out;
}

export function buildRequest(c: LinkedInConfig, text: string) {
  const headers: Record<string, string> = {
    "Linkedin-Version": c.apiVersion,
    "X-Restli-Protocol-Version": "2.0.0",
    "Content-Type": "application/json",
  };
  const body = {
    author: c.personUrn,
    commentary: toLittle(stripText(text)),
    visibility: c.visibility,
    distribution: { feedDistribution: "MAIN_FEED", targetEntities: [], thirdPartyDistributionChannels: [] },
    lifecycleState: "PUBLISHED",
    isReshareDisabledByAuthor: false,
  };
  return { url: POSTS_URL, headers, body };
}

export function postUrl(urn: string): string {
  return `https://www.linkedin.com/feed/update/${urn}/`;
}

async function errorText(resp: Response): Promise<string> {
  try {
    const data = JSON.parse(await resp.text()) as Record<string, unknown>;
    return ["code", "serviceErrorCode", "message"].filter((k) => data?.[k]).map((k) => String(data[k]))
      .join(" ").slice(0, 300);
  } catch {
    return "";
  }
}

export async function mapResponse(resp: Response): Promise<PublishResult> {
  const status = resp.status;
  const detail = { http_status: status, sent: true, message: await errorText(resp) };
  if (status === 201) {
    const urn = (resp.headers.get("x-restli-id") ?? "").trim();
    if (POST_URN_RE.test(urn)) return { outcome: "published", remoteId: urn, url: postUrl(urn), detail };
    return { outcome: "ambiguous", detail: { ...detail, reason: "created without a recognizable post URN" } };
  }
  if (REJECTED.has(status)) return { outcome: "rejected", detail: { ...detail, reason: `http_${status}`, retryable: false } };
  if (status === 429) return { outcome: "rejected", detail: { ...detail, reason: "rate_limited", retryable: true } };
  return { outcome: "ambiguous", detail: { ...detail, reason: `http_${status}` } };
}

// Exactly one request. Never retried here or anywhere else.
export async function publish(c: LinkedInConfig, token: string, text: string, fetchImpl: FetchLike,
                              timeoutMs = 25000): Promise<PublishResult> {
  const { url, headers, body } = buildRequest(c, text);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetchImpl(url, {
      method: "POST", headers: { ...headers, Authorization: `Bearer ${token}` },
      body: JSON.stringify(body), signal: ctrl.signal,
    });
    return await mapResponse(resp);
  } catch (err) {
    return { outcome: "ambiguous", detail: { reason: `transport:${(err as Error)?.name ?? "Error"}`, sent: true } };
  } finally {
    clearTimeout(timer);
  }
}
