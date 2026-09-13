import { describe, it, expect, vi, afterEach } from "vitest";
import { api, setCsrf, browseSql } from "./api";
afterEach(() => vi.unstubAllGlobals());
describe("API boundary", () => {
  it("sends CSRF and same origin credentials", async () => {
    setCsrf("csrf");
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ result: { rows: [] } }),
    });
    vi.stubGlobal("fetch", fetch);
    await api("query", { sql: "SELECT 1" });
    expect(fetch.mock.calls[0][1]).toMatchObject({
      credentials: "same-origin",
      headers: expect.objectContaining({ "X-CSRFToken": "csrf" }),
    });
  });
  it("keeps server errors out of successful results", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 403,
        json: async () => ({ error: { message: "Sin permiso" } }),
      }),
    );
    await expect(api("catalog")).rejects.toMatchObject({
      status: 403,
      message: "Sin permiso",
    });
  });
  it("quotes database identifiers and bounds pages", () => {
    expect(browseSql({ name: 'a"b', primary_key: ["id"] } as never, 0)).toBe(
      'SELECT * FROM data."a""b" ORDER BY "id" LIMIT 51 OFFSET 0',
    );
    expect(() =>
      browseSql({ name: "t", primary_key: [] } as never, -1),
    ).toThrow();
  });
});
