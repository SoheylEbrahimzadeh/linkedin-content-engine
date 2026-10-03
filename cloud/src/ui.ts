// Remote dashboard (Phase 4C): static files served by the Worker, behind the
// same fail-closed Cloudflare Access check as the API. The page holds no data;
// it reads /api/snapshot like any other client.

import { AuthError, verifyAccess, type CertsFetcher } from "./auth";
import type { Env } from "./db";
import css from "./ui/app.css";
import js from "./ui/app.txt";
import lib from "./ui/lib.txt";
import html from "./ui/index.html";
import pipelineJs from "./pipeline/app.txt";
import pipelineHtml from "./pipeline/index.html";
import pipelineLib from "./pipeline/lib.txt";
import pipelineCss from "./pipeline/styles.css";

// LCE-045: one identifier per UI build. A page left open across a deploy compares it with the
// snapshot's ui_build and reloads, so it never shows old code next to new data.
function fnv1a(text: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 0x01000193) >>> 0;
  return h.toString(16).padStart(8, "0");
}
export const UI_BUILD = fnv1a(html + css + js + lib);
const appJs = js.replaceAll("__UI_BUILD__", UI_BUILD);

const CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; " +
  "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
const FILES: Record<string, [string, string]> = {
  "/": [html, "text/html; charset=utf-8"],
  "/app.css": [css, "text/css; charset=utf-8"],
  "/app.js": [appJs, "text/javascript; charset=utf-8"],
  "/lib.js": [lib, "text/javascript; charset=utf-8"],
  // LCE-013: the Web Control Center (same files as `lce dashboard serve`), reading
  // the private-pipeline mirror from D1 instead of the local data directory.
  "/pipeline/": [pipelineHtml, "text/html; charset=utf-8"],
  "/pipeline/styles.css": [pipelineCss, "text/css; charset=utf-8"],
  "/pipeline/app.js": [pipelineJs, "text/javascript; charset=utf-8"],
  "/pipeline/lib.js": [pipelineLib, "text/javascript; charset=utf-8"],
  "/pipeline/config.js": ['window.LCE_CONFIG = {"mode": "real", "snapshotUrl": "/api/pipeline"};\n',
    "text/javascript; charset=utf-8"],
};

export function isUiPath(path: string): boolean {
  return path in FILES || path === "/pipeline";
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
  const path = new URL(request.url).pathname;
  if (path === "/pipeline") return new Response(null, { status: 301, headers: { ...headers, Location: "/pipeline/" } });
  const [body, type] = FILES[path];
  return new Response(body, { status: 200, headers: { ...headers, "Content-Type": type } });
}
