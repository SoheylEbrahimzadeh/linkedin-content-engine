// Port of lce.publish.linkedin (official Posts API). Same request shape and the
// same outcome mapping, enforced by shared test vectors. One difference is
// deliberate: a Worker cannot tell whether a failed fetch reached LinkedIn, so
// every transport error is AMBIGUOUS (never "not sent").

import { stripText, toLittle } from "./text";

export const POSTS_URL = "https://api.linkedin.com/rest/posts";
export const IMAGES_INIT_URL = "https://api.linkedin.com/rest/images?action=initializeUpload";
export const UPLOAD_PREFIX = "https://www.linkedin.com/dms-uploads/";
const IMAGE_URN_RE = /^urn:li:image:[A-Za-z0-9_-]+$/;
const POST_URN_RE = /^urn:li:(share|ugcPost):\d+$/;
const PERSON_URN_RE = /^urn:li:person:[A-Za-z0-9_-]+$/;
const REJECTED = new Set([400, 401, 403, 404, 422]);

export type LinkedInConfig = { apiVersion: string; personUrn: string; visibility: string; maxChars: number };
export type Outcome = "published" | "rejected" | "ambiguous";
export type PublishResult = {
  outcome: Outcome; remoteId?: string; url?: string;
  detail: { http_status?: number; reason?: string; message?: string; retryable?: boolean; sent: boolean;
            image_urn?: string };
};
export type ImageInput = { data: Uint8Array; alt: string };
export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

export function configProblems(c: LinkedInConfig): string[] {
  const out: string[] = [];
  if (!/^\d{6}$/.test(c.apiVersion ?? "")) out.push("api_version must be YYYYMM");
  if (!PERSON_URN_RE.test(c.personUrn ?? "")) out.push("person_urn must look like urn:li:person:<id>");
  if (!["PUBLIC", "CONNECTIONS", "LOGGED_IN"].includes(c.visibility)) out.push("invalid visibility");
  return out;
}

export function buildRequest(c: LinkedInConfig, text: string, image?: { urn: string; alt: string }) {
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
  } as Record<string, unknown>;
  if (image) body.content = { media: { id: image.urn, altText: image.alt.trim() } };
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

// Image upload before the post (Images API). Any failure here created no post:
// a plain, retryable rejection. The token only goes to api.linkedin.com and to
// LinkedIn's dms-uploads URL.
async function uploadImage(c: LinkedInConfig, token: string, image: ImageInput, fetchImpl: FetchLike,
                           timeoutMs: number): Promise<string | PublishResult> {
  const failed = (reason: string, extra: Record<string, unknown> = {}): PublishResult =>
    ({ outcome: "rejected", detail: { reason, sent: false, retryable: true, ...extra } });
  const call = async (url: string, init: RequestInit) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
    try {
      return await fetchImpl(url, { ...init, signal: ctrl.signal });
    } finally {
      clearTimeout(timer);
    }
  };
  let init: Response;
  try {
    init = await call(IMAGES_INIT_URL, {
      method: "POST",
      headers: { "Linkedin-Version": c.apiVersion, "X-Restli-Protocol-Version": "2.0.0",
                 "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify({ initializeUploadRequest: { owner: c.personUrn } }),
    });
  } catch (err) {
    return failed("image_init_transport", { message: (err as Error)?.name });
  }
  if (init.status !== 200) return failed("image_init_failed", { http_status: init.status });
  let uploadUrl = "", urn = "";
  try {
    const value = ((await init.json()) as { value: { uploadUrl: string; image: string } }).value;
    uploadUrl = String(value.uploadUrl);
    urn = String(value.image);
  } catch {
    return failed("image_init_unreadable");
  }
  if (!uploadUrl.startsWith(UPLOAD_PREFIX) || !IMAGE_URN_RE.test(urn)) return failed("image_init_untrusted");
  let up: Response;
  try {
    up = await call(uploadUrl, { method: "PUT", headers: { Authorization: `Bearer ${token}` }, body: image.data });
  } catch (err) {
    return failed("image_upload_transport", { message: (err as Error)?.name });
  }
  if (up.status !== 200 && up.status !== 201) return failed("image_upload_failed", { http_status: up.status });
  return urn;
}

// Exactly one post request. Never retried here or anywhere else.
export async function publish(c: LinkedInConfig, token: string, text: string, fetchImpl: FetchLike,
                              timeoutMs = 25000, image?: ImageInput): Promise<PublishResult> {
  let imageRef: { urn: string; alt: string } | undefined;
  if (image) {
    const uploaded = await uploadImage(c, token, image, fetchImpl, timeoutMs);
    if (typeof uploaded !== "string") return uploaded;
    imageRef = { urn: uploaded, alt: image.alt };
  }
  const { url, headers, body } = buildRequest(c, text, imageRef);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetchImpl(url, {
      method: "POST", headers: { ...headers, Authorization: `Bearer ${token}` },
      body: JSON.stringify(body), signal: ctrl.signal,
    });
    const result = await mapResponse(resp);
    return imageRef ? { ...result, detail: { ...result.detail, image_urn: imageRef.urn } } : result;
  } catch (err) {
    return { outcome: "ambiguous", detail: { reason: `transport:${(err as Error)?.name ?? "Error"}`, sent: true } };
  } finally {
    clearTimeout(timer);
  }
}
