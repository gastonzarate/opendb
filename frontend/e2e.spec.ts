import { test, expect, type Page } from "@playwright/test";
const db = {
  id: "11111111-1111-1111-1111-111111111111",
  status: "ready",
  is_owner: true,
};
const object = {
  name: "gastos",
  kind: "table",
  description: "Gastos por categoría y moneda.",
  primary_key: ["id"],
  foreign_keys: [],
  columns: [
    { name: "id", type: "bigint", nullable: false },
    { name: "concepto", type: "text", nullable: false },
    { name: "categoria", type: "text", nullable: true },
    { name: "importe", type: "numeric(12,2)", nullable: false },
    { name: "moneda", type: "text", nullable: false },
  ],
};
async function fixture(page: Page, guest = false) {
  await page.route("**/api/bootstrap/", (r) =>
    r.fulfill({
      json: {
        csrf_token: "test-csrf",
        google_configured: true,
        mcp_url: "http://localhost:8001/mcp",
      },
    }),
  );
  await page.route("**/api/session/", (r) =>
    r.fulfill({
      json: {
        user: { id: 1, email: "prueba@example.com" },
        csrf_token: "test-csrf",
      },
    }),
  );
  const roles: {
    id: string;
    name: string;
    objects: string[];
    emails: string[];
  }[] = [];
  await page.route("**/api/actions/*/", async (r) => {
    const action = new URL(r.request().url()).pathname.split("/").at(-2);
    const p = r.request().postDataJSON();
    let result: unknown = null;
    if (action === "list_databases") result = [{ ...db, is_owner: !guest }];
    if (action === "catalog")
      result = { fingerprint: "test", objects: [object] };
    if (action === "query")
      result = {
        columns: ["id", "concepto", "categoria", "importe", "moneda"],
        rows: [
          [1, "Supermercado", "Alimentación", "125.40", "USD"],
          [2, "Internet", "Servicios", "42.00", "USD"],
          [3, "Libro de diseño", "Educación", "28.50", "USD"],
        ],
        truncated: false,
      };
    if (action === "list_access") result = roles;
    if (action === "create_role") {
      const role = { id: "role-1", name: p.name, objects: [], emails: [] };
      roles.push(role);
      result = role;
    }
    if (action === "grant_object") roles[0].objects.push(p.object_name);
    if (action === "assign_role") roles[0].emails.push(p.email);
    if (action === "vector_status") result = [];
    await r.fulfill({ json: { result } });
  });
}
test("real signed-out page gets CSRF and shows Google login", async ({
  page,
}) => {
  await page.goto("/app/");
  await expect(
    page.getByRole("button", { name: "Continuar con Google" }),
  ).toBeVisible();
  await expect(page.locator("input[name=csrfmiddlewaretoken]")).not.toHaveValue(
    "",
  );
  await page.screenshot({ path: "test-results/login.png", fullPage: true });
});
test("explorer, SQL and sharing use API actions", async ({ page }) => {
  await fixture(page);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/app/");
  await expect(page.getByText("Supermercado", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/explorer.png", fullPage: true });
  await page.getByRole("tab", { name: "Estructura" }).click();
  await expect(page.getByText("Primary key", { exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Consulta SQL" }).click();
  await page
    .getByLabel("Consulta o cambio de esquema")
    .fill("SELECT * FROM data.gastos LIMIT 5");
  await page.getByRole("button", { name: "Ejecutar", exact: true }).click();
  await expect(page.getByText("Supermercado", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Accesos", exact: true }).click();
  await page.getByLabel("Nombre del rol").fill("Contador");
  await page.getByRole("button", { name: "Crear rol" }).click();
  await expect(page.getByRole("heading", { name: "Contador" })).toBeVisible();
  await page.screenshot({ path: "test-results/access.png", fullPage: true });
  expect(errors).toEqual([]);
});
test("guest sees only read controls on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, true);
  await page.goto("/app/");
  await expect(page.getByText("Solo lectura", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Accesos", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("Supermercado", { exact: true })).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({ path: "test-results/mobile.png", fullPage: true });
});
test("a revoked table reports error rather than displaying cached rows", async ({
  page,
}) => {
  await fixture(page);
  await page.goto("/app/");
  await expect(page.getByText("Supermercado", { exact: true })).toBeVisible();
  await page.route("**/api/actions/query/", (r) =>
    r.fulfill({ status: 403, json: { error: { message: "Acceso revocado" } } }),
  );
  await page.getByRole("button", { name: "Actualizar registros" }).click();
  await expect(page.getByRole("alert")).toContainText("Acceso revocado");
  await expect(page.getByText("Supermercado", { exact: true })).toHaveCount(0);
});
