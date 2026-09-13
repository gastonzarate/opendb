import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: ".",
  testMatch: "e2e.spec.ts",
  use: {
    baseURL: process.env.OPENDB_WEB_URL || "http://localhost:8000",
    headless: true,
    viewport: { width: 1440, height: 960 },
  },
  workers: 1,
});
