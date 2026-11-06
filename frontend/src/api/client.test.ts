import { afterEach, expect, it, vi } from "vitest";

import { ApiError, postLedgerEntry } from "./client";

afterEach(() => vi.unstubAllGlobals());

it("sends only the idempotency key from the browser, never an auth token", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true, json: async () => ({ id: 1 }),
  });
  vi.stubGlobal("fetch", fetchMock);
  await postLedgerEntry("buy", { symbol: "INFY", quantity: 1 }, "key-1");
  expect(fetchMock).toHaveBeenCalledWith("/api/v1/portfolio/buys", {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": "key-1" },
    body: '{"symbol":"INFY","quantity":1}',
  });
});

it("turns backend validation details into readable field errors", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: false, status: 422,
    json: async () => ({ detail: [{ loc: ["body", "quantity"], msg: "must be positive" }] }),
  }));
  await expect(postLedgerEntry("sell", {}, "key-2"))
    .rejects.toEqual(new ApiError(422, "quantity: must be positive"));
});
