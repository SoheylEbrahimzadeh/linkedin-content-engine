import { describe, expect, it } from "vitest";
import { buildRequest, mapResponse } from "../src/linkedin";
import { loadSchedule, slotsBetween } from "../src/schedule";
import { contentHash, normalizeText, stripText, toLittle } from "../src/text";
import vectors from "./vectors.json";

describe("parity with the Python reference implementation", () => {
  it.each(vectors.texts)("normalize + hash + little: %#", async (v) => {
    expect(normalizeText(v.text)).toBe(v.normalized);
    expect(await contentHash(v.text)).toBe(v.hash);
    expect(toLittle(stripText(v.text))).toBe(v.little);
  });

  it.each(vectors.schedules)("slots incl. DST: %#", (v) => {
    const slots = slotsBetween(loadSchedule(v.settings), Date.parse(v.start), Date.parse(v.end));
    expect(slots).toEqual(v.slots);
  });

  it.each(vectors.responses)("LinkedIn response mapping %#", async (v) => {
    const r = await mapResponse(new Response(v.status === 201 || v.status === 200 || v.status === 302 ? null : "{}",
      { status: v.status, headers: v.headers as Record<string, string> }));
    expect(r.outcome).toBe(v.outcome);
    expect(r.remoteId ?? null).toBe(v.remote_id);
    expect(r.detail.retryable ?? null).toBe(v.retryable ?? null);
  });

  it("request shape", () => {
    const { headers, body } = buildRequest({ apiVersion: "202609", personUrn: "urn:li:person:Vector1",
      visibility: "PUBLIC", maxChars: 3000 }, vectors.request.text);
    expect(headers).toEqual(vectors.request.headers);
    expect(body).toEqual(vectors.request.body);
  });
});
