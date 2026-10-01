// Cloudflare Access JWT verification (RS256). Fail-closed: without a configured
// Access application every API request is refused.

export type Identity = { subject: string };
export type CertsFetcher = (teamDomain: string) => Promise<{ keys: JsonWebKey[] }>;

let certsCache: { domain: string; at: number; keys: JsonWebKey[] } | null = null;

export const defaultCertsFetcher: CertsFetcher = async (team) => {
  const resp = await fetch(`https://${team}/cdn-cgi/access/certs`);
  if (!resp.ok) throw new Error(`certs HTTP ${resp.status}`);
  return (await resp.json()) as { keys: JsonWebKey[] };
};

function b64urlToBytes(s: string): Uint8Array {
  const b64 = s.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((s.length + 3) % 4);
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}

const TEAM_RE = /^[a-z0-9-]+\.cloudflareaccess\.com$/;
const AUD_RE = /^[0-9a-f]{64}$/;

export class AuthError extends Error {
  constructor(message: string, readonly status = 401) {
    super(message);
  }
}

type AccessEnv = { ACCESS_TEAM_DOMAIN?: string; ACCESS_AUD?: string;
  LCE_ACCESS_TEAM_DOMAIN?: string; LCE_ACCESS_AUD?: string };

// The pair of secrets wins when both are set; otherwise the public pair that every
// deploy carries in wrangler.toml [vars] (LCE-031). Never mixed.
export function accessConfig(env: AccessEnv): { team: string; aud: string } {
  const pair = (t?: string, a?: string) => ({ team: (t ?? "").trim(), aud: (a ?? "").trim().toLowerCase() });
  const secret = pair(env.ACCESS_TEAM_DOMAIN, env.ACCESS_AUD);
  return secret.team && secret.aud ? secret : pair(env.LCE_ACCESS_TEAM_DOMAIN, env.LCE_ACCESS_AUD);
}

export async function verifyAccess(request: Request, env: AccessEnv,
                                   nowMs: number, fetchCerts: CertsFetcher = defaultCertsFetcher): Promise<Identity> {
  const { team, aud } = accessConfig(env);
  if (!team || !aud) throw new AuthError("Cloudflare Access is not configured", 503);
  // The team domain decides where signing keys are fetched from: accept only an Access team domain.
  if (!TEAM_RE.test(team) || !AUD_RE.test(aud)) throw new AuthError("Cloudflare Access is misconfigured", 503);
  const token = request.headers.get("cf-access-jwt-assertion");
  if (!token) throw new AuthError("missing Cloudflare Access token");
  const parts = token.split(".");
  if (parts.length !== 3) throw new AuthError("malformed token");
  let header: { alg?: string; kid?: string }, payload: Record<string, unknown>;
  try {
    header = JSON.parse(new TextDecoder().decode(b64urlToBytes(parts[0])));
    payload = JSON.parse(new TextDecoder().decode(b64urlToBytes(parts[1])));
  } catch {
    throw new AuthError("malformed token");
  }
  if (header.alg !== "RS256") throw new AuthError("unexpected algorithm");
  if (!certsCache || certsCache.domain !== team || nowMs - certsCache.at > 600e3) {
    certsCache = { domain: team, at: nowMs, keys: (await fetchCerts(team)).keys };
  }
  const jwk = certsCache.keys.find((k) => (k as { kid?: string }).kid === header.kid);
  if (!jwk) throw new AuthError("unknown signing key");
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false, ["verify"]);
  const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlToBytes(parts[2]),
    new TextEncoder().encode(`${parts[0]}.${parts[1]}`));
  if (!ok) throw new AuthError("bad signature");
  const audiences = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
  if (!audiences.includes(aud)) throw new AuthError("wrong audience");
  if (payload.iss !== `https://${team}`) throw new AuthError("wrong issuer");
  const now = Math.floor(nowMs / 1000);
  if (typeof payload.exp !== "number" || payload.exp < now) throw new AuthError("token expired");
  if (typeof payload.nbf === "number" && payload.nbf > now + 60) throw new AuthError("token not yet valid");
  const subject = String(payload.email ?? payload.common_name ?? payload.sub ?? "unknown");
  return { subject };
}

export function resetCertsCache(): void {
  certsCache = null;
}
