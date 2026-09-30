import { applyD1Migrations, env } from "cloudflare:test";
import { beforeEach } from "vitest";

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

// No test may reach the network. Everything external is injected.
beforeEach(() => {
  globalThis.fetch = (() => {
    throw new Error("network access is blocked in tests");
  }) as typeof fetch;
});
