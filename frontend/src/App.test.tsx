import { render, screen } from "@testing-library/react";
import { it, expect, vi, afterEach } from "vitest";
import App from "./App";
afterEach(() => vi.unstubAllGlobals());
it("signed-out users see Google login, not invented data", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.includes("bootstrap")
        ? {
            ok: true,
            json: async () => ({
              csrf_token: "csrf",
              google_configured: true,
              mcp_url: "http://localhost:8001/mcp",
            }),
          }
        : { ok: false, status: 401, json: async () => ({}) },
    ),
  );
  render(<App />);
  expect(
    await screen.findByRole("button", { name: "Continuar con Google" }),
  ).toBeEnabled();
  expect(screen.queryByRole("table")).not.toBeInTheDocument();
});
