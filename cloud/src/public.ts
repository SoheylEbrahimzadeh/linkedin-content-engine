// Public pages (no Cloudflare Access check in the Worker): only the privacy policy,
// which LinkedIn's Developer Portal must be able to open. Static, no scripts, no data.

import privacy from "./public/privacy.html";

const CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; " +
  "frame-ancestors 'none'";

export function isPublicPath(path: string): boolean {
  return path === "/privacy";
}

export function handlePublic(request: Request): Response {
  const headers = { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "public, max-age=300",
    "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY" };
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { ...headers, Allow: "GET, HEAD" } });
  }
  return new Response(request.method === "HEAD" ? null : privacy, { status: 200, headers });
}
