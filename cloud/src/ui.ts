// Remote dashboard (Phase 4C): static files served by the Worker, behind the
// same fail-closed Cloudflare Access check as the API. The page holds no data;
// it reads /api/snapshot like any other client.

import { AuthError, verifyAccess, type CertsFetcher } from "./auth";
import type { Env } from "./db";
import css from "./ui/app.css";
import js from "./ui/app.txt";
import html from "./ui/index.html";

const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; " +
  "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
const FILES: Record<string, [string, string]> = {
  "/": [html, "text/html; charset=utf-8"],
  "/app.css": [css, "text/css; charset=utf-8"],
  "/app.js": [js, "text/javascript; charset=utf-8"],
};

export function isUiPath(path: string): boolean {
  return path in FILES;
}

export async function handleUi(request: Request, env: Env, now: number, certs?: CertsFetcher): Promise<Response> {
  const headers = { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": CSP, "X-Frame-Options": "DENY" };
  if (request.method !== "GET") return new Response("Method not allowed", { status: 405, headers });
  try {
    await verifyAccess(request, env, now, certs);
  } catch (err) {
    const status = err instanceof AuthError ? err.status : 500;
    return new Response(status === 503 ? "Access is not configured; the dashboard is disabled." : "Not authorized.",
      { status, headers: { ...headers, "Content-Type": "text/plain; charset=utf-8" } });
  }
  const [body, type] = FILES[new URL(request.url).pathname];
  return new Response(body, { status: 200, headers: { ...headers, "Content-Type": type } });
}
