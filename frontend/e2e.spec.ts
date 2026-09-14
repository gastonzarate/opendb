import { test, expect, type Page } from "@playwright/test";
const db = {
  id: "11111111-1111-1111-1111-111111111111",
  status: "ready",
  is_owner: true,
  onboarding_completed: true,
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
async function fixture(page: Page, guest = false, onboarding = false) {
  let completed = !onboarding;
  await page.route("**/api/bootstrap/", (r) =>
    r.fulfill({
      json: {
        csrf_token: "test-csrf",
        google_configured: true,
        mcp_url: "https://mcp.example.com/mcp",
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
    description: string;
    objects: string[];
    emails: string[];
  }[] = [];
  await page.route("**/api/actions/*/", async (r) => {
    const action = new URL(r.request().url()).pathname.split("/").at(-2);
    const p = r.request().postDataJSON();
    let result: unknown = null;
    if (action === "list_databases")
      result = guest
        ? [
            { ...db, id: "own", onboarding_completed: true },
            { ...db, is_owner: false },
          ]
        : [{ ...db, onboarding_completed: completed }];
    if (action === "complete_onboarding") {
      completed = true;
      result = { ...db, onboarding_completed: true };
    }
    if (action === "catalog")
      result = {
        fingerprint: "test",
        objects: [
          object,
          { ...object, name: "ingresos", description: "Ingresos registrados." },
        ],
      };
    if (action === "query")
      result = p.sql.includes('data."ingresos"')
        ? { columns: ["id", "concepto"], rows: [], truncated: false }
        : {
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
      const role = {
        id: "role-1",
        name: p.name,
        description: p.description,
        objects: [],
        emails: [],
      };
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
  await page
    .getByLabel("Descripción del rol")
    .fill("Consulta gastos e ingresos para preparar mis impuestos.");
  await page.getByRole("button", { name: "Crear rol" }).click();
  await expect(page.getByRole("heading", { name: "Contador" })).toBeVisible();
  await page.screenshot({ path: "test-results/access.png", fullPage: true });
  expect(errors).toEqual([]);
});
test("guest sees only read controls on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, true);
  await page.goto("/app/");
  await page.getByRole("button", { name: "Abrir navegación" }).click();
  await page.getByLabel("BASE DE DATOS", { exact: true }).selectOption(db.id);
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

test("first login explains connection and completing onboarding persists across reload", async ({
  page,
}) => {
  await fixture(page, false, true);
  await page.goto("/app/");
  await expect(
    page.getByRole("heading", { name: "Conectá tu asistente" }),
  ).toBeVisible();
  await expect(page.getByLabel("Buscar tablas y vistas")).toHaveCount(0);
  await expect(page.getByLabel("Comando de Claude Code")).toHaveValue(
    "claude mcp add --transport http --scope user opendb 'https://mcp.example.com/mcp'",
  );
  await expect(page.getByText(/túnel|Tailscale|Google Console/)).toHaveCount(0);
  await page.screenshot({
    path: "test-results/onboarding.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Ir a mis datos" }).click();
  await expect(
    page.getByRole("heading", { name: "Mis datos", exact: true }),
  ).toBeVisible();
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "Mis datos", exact: true }),
  ).toBeVisible();
});

test("onboarding remains usable on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page, false, true);
  await page.goto("/app/");
  await expect(page.getByRole("tab", { name: "Claude Code" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(
    page.getByRole("button", { name: "Ir a mis datos" }),
  ).toBeEnabled();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/onboarding-mobile.png",
    fullPage: true,
  });
});

for (const mobile of [false, true]) {
  test(`sidebar changes table ${mobile ? "mobile" : "desktop"}`, async ({
    page,
  }) => {
    if (mobile) await page.setViewportSize({ width: 390, height: 844 });
    await fixture(page);
    await page.goto("/app/");
    await expect(page.getByText("Supermercado", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "Tabla o vista" }),
    ).toHaveCount(0);
    if (mobile)
      await page.getByRole("button", { name: "Abrir navegación" }).click();
    await page.getByRole("button", { name: "ingresos", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "ingresos", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText("No hay registros para mostrar."),
    ).toBeVisible();
    if (mobile)
      await page.getByRole("button", { name: "Abrir navegación" }).click();
    await page.getByRole("button", { name: "gastos", exact: true }).click();
    await expect(page.getByText("Supermercado", { exact: true })).toBeVisible();
  });
}

test("database deletion confirms, clears data and stays deleted after reload", async ({
  page,
}) => {
  await fixture(page);
  let deleted = false;
  await page.route("**/api/actions/delete_database/", async (route) => {
    expect(route.request().postDataJSON()).toEqual({ database_id: db.id });
    deleted = true;
    await route.fulfill({ json: { result: { ...db, status: "deleted" } } });
  });
  await page.route("**/api/actions/list_databases/", (route) =>
    route.fulfill({
      json: { result: [{ ...db, status: deleted ? "deleted" : "ready" }] },
    }),
  );
  await page.goto("/app/");
  await expect(page.getByText("Supermercado", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Borrar base de datos" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "Cancelar", exact: true }).click();
  expect(deleted).toBe(false);
  await page.getByRole("button", { name: "Borrar base de datos" }).click();
  await page.getByRole("button", { name: "Borrar definitivamente" }).click();
  await expect(
    page.getByRole("heading", { name: "Base eliminada" }),
  ).toBeVisible();
  await expect(page.getByText("Supermercado", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Crear base vacía" }),
  ).toBeVisible();
  await page.screenshot({ path: "test-results/deleted.png", fullPage: true });
});

test("failed deletion hides cached data and offers retry", async ({ page }) => {
  await fixture(page);
  await page.route("**/api/actions/delete_database/", (route) =>
    route.fulfill({ status: 500, json: { error: { message: "Failed" } } }),
  );
  await page.goto("/app/");
  await expect(page.getByText("Supermercado", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Borrar base de datos" }).click();
  await page.getByRole("button", { name: "Borrar definitivamente" }).click();
  await expect(
    page.getByRole("heading", { name: "El borrado todavía no terminó" }),
  ).toBeVisible();
  await expect(page.getByText("Supermercado", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Borrar base de datos" }),
  ).toBeVisible();
});
