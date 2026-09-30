// LCE cloud Worker: cron-driven publishing of human-approved, consented posts
// plus an Access-protected JSON API and the remote dashboard (Phase 4C).

import { handleApi } from "./api";
import type { Env } from "./db";
import { runScheduled } from "./runner";
import { handleUi, isUiPath } from "./ui";

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/")) return handleApi(request, env, Date.now());
    if (isUiPath(url.pathname)) return handleUi(request, env, Date.now());
    return new Response("Not found", { status: 404, headers: { "Cache-Control": "no-store" } });
  },

  async scheduled(_controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(runScheduled(env, Date.now(), (input, init) => fetch(input, init)).then((report) => {
      if (report.status !== "idle") console.log(JSON.stringify({ cron: report.status, post: report.post_id ?? null }));
    }));
  },
};
