// Byte-for-byte ports of lce.textutil.normalize_text / content_hash and
// lce.publish.little.to_little. Parity is enforced by shared test vectors.

// Characters Python's str.isspace() treats as whitespace (used by strip/rstrip).
const PY_WS = "\t\n\u000b\u000c\r\u001c\u001d\u001e\u001f \u0085                 　";
const isWs = (ch: string) => PY_WS.includes(ch);

function rstrip(s: string): string {
  let end = s.length;
  while (end > 0 && isWs(s[end - 1])) end--;
  return s.slice(0, end);
}

function strip(s: string): string {
  let start = 0;
  while (start < s.length && isWs(s[start])) start++;
  return rstrip(s.slice(start));
}

export function normalizeText(text: string): string {
  const t = text.normalize("NFC").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  return strip(t.split("\n").map(rstrip).join("\n")) + "\n";
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function sha256Bytes(data: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export async function contentHash(text: string): Promise<string> {
  return sha256Hex(normalizeText(text));
}

const RESERVED = new Set("|{}@[]()<>#\\*_~");
// A '#' that starts a hashtag: at string start or after whitespace, followed by a
// letter/digit (Python: (?:(?<=^)|(?<=\s))#(?=[^\W_])).
function hashtagPositions(text: string): Set<number> {
  const out = new Set<number>();
  const chars = [...text];
  let index = 0;
  for (let i = 0; i < chars.length; i++) {
    if (chars[i] === "#" && (i === 0 || isWs(chars[i - 1])) && i + 1 < chars.length &&
        /[\p{L}\p{N}]/u.test(chars[i + 1])) {
      out.add(index);
    }
    index += chars[i].length;
  }
  return out;
}

export function toLittle(text: string): string {
  const tags = hashtagPositions(text);
  let out = "";
  let index = 0;
  for (const ch of text) {
    out += RESERVED.has(ch) && !tags.has(index) ? "\\" + ch : ch;
    index += ch.length;
  }
  return out;
}

export function stripText(text: string): string {
  return strip(text);
}

// LCE-053 (owner rule 2026-10-06): an image's attribution is internal provenance only. A whole line
// labelling an image ("Image: X", "Photo credit: X", "Credit: X", ...) or a source label line
// ("Source: X", "Sources: X", "Via: X") is never published. Mirrors lce.credit (Python).
const VISIBLE_CREDIT_RE = new RegExp(
  String.raw`^\s*(?:(?:(?:header|cover|featured|hero|lead)\s+)?(?:images?|photos?|pictures?|illustrations?|graphics?|visuals?|credits?)` +
  String.raw`(?:\s+(?:source|credit|courtesy|by)s?)?|sources?|via)\s*:\s*\S.*$`, "im");

/** The first visible credit/source label line in a post text, or null. */
export function visibleCredit(text: string): string | null {
  const m = VISIBLE_CREDIT_RE.exec(text);
  return m ? m[0].trim() : null;
}
