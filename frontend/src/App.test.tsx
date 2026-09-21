import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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

it("local login form only appears when the server enables it", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.includes("bootstrap")
        ? {
            ok: true,
            json: async () => ({
              csrf_token: "csrf",
              google_configured: true,
              local_login_enabled: true,
              mcp_url: "http://localhost:8001/mcp",
            }),
          }
        : { ok: false, status: 401, json: async () => ({}) },
    ),
  );
  render(<App />);
  await screen.findByRole("button", { name: "Continuar con Google" });
  expect(screen.getByLabelText("Email")).toBeInTheDocument();
  expect(screen.getByLabelText("Contraseña")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Entrar con email y contraseña" }),
  ).toBeInTheDocument();
});
it("local login form is absent by default", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) =>
      url.includes("bootstrap")
        ? {
            ok: true,
            json: async () => ({
              csrf_token: "csrf",
              google_configured: true,
              local_login_enabled: false,
              mcp_url: "http://localhost:8001/mcp",
            }),
          }
        : { ok: false, status: 401, json: async () => ({}) },
    ),
  );
  render(<App />);
  await screen.findByRole("button", { name: "Continuar con Google" });
  expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
});
function authenticatedFixture(
  own: Record<string, unknown> | null,
  shared = false,
) {
  const ownDb = {
    id: "own",
    status: "ready",
    is_owner: true,
    onboarding_completed: false,
    ...own,
  };
  let databases = own
    ? [ownDb]
    : shared
      ? [
          {
            id: "shared",
            status: "ready",
            is_owner: false,
            onboarding_completed: true,
          },
        ]
      : [];
  const fetch = vi.fn(async (url: string) => {
    let data: unknown;
    if (url.includes("bootstrap"))
      data = {
        csrf_token: "csrf",
        google_configured: true,
        mcp_url: "http://localhost:8001/mcp",
      };
    else if (url.includes("session"))
      data = {
        user: { id: 1, email: "owner@example.com" },
        csrf_token: "csrf",
      };
    else {
      let result: unknown;
      if (url.includes("list_databases")) result = databases;
      if (url.includes("create_database")) {
        databases = [ownDb, ...databases];
        result = ownDb;
      }
      if (url.includes("catalog")) result = { fingerprint: "x", objects: [] };
      if (url.includes("complete_onboarding")) {
        ownDb.onboarding_completed = true;
        result = ownDb;
      }
      data = { result };
    }
    return { ok: true, json: async () => data };
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
it("creates a missing own database automatically even when shared databases exist", async () => {
  const fetch = authenticatedFixture(null, true);
  render(<App />);
  await screen.findByRole("heading", { name: "Conectá tu asistente" });
  expect(
    fetch.mock.calls.some(([url]) => url.includes("create_database")),
  ).toBe(true);
  expect(
    screen.queryByRole("button", { name: /Crear.*base/ }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("textbox", { name: "Buscar tablas y vistas" }),
  ).not.toBeInTheDocument();
});
it("first login opens connection while a completed onboarding opens data", async () => {
  const fetch = authenticatedFixture({ onboarding_completed: false });
  render(<App />);
  await screen.findByRole("heading", { name: "Conectá tu asistente" });
  expect(
    fetch.mock.calls.some(([url]) => url.includes("create_database")),
  ).toBe(false);
});
it("returning users open their data", async () => {
  authenticatedFixture({ onboarding_completed: true });
  render(<App />);
  await screen.findByRole("heading", { name: "Mis datos" });
  expect(
    screen.getByRole("textbox", { name: "Buscar tablas y vistas" }),
  ).toBeInTheDocument();
});

it("acknowledges onboarding on the server before opening data", async () => {
  const fetch = authenticatedFixture({ onboarding_completed: false });
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Ir a mis datos" }),
  );
  await screen.findByRole("heading", { name: "Mis datos" });
  expect(
    fetch.mock.calls.some(([url]) => url.includes("complete_onboarding")),
  ).toBe(true);
});
it("keeps onboarding visible when saving completion fails", async () => {
  const fetch = authenticatedFixture({ onboarding_completed: false });
  const original = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (url: string) =>
    url.includes("complete_onboarding")
      ? {
          ok: false,
          status: 500,
          json: async () => ({ error: { message: "No pudimos guardar" } }),
        }
      : original(url),
  );
  render(<App />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Ir a mis datos" }),
  );
  await waitFor(() =>
    expect(screen.getByRole("alert")).toHaveTextContent("No pudimos guardar"),
  );
  expect(
    screen.getByRole("heading", { name: "Conectá tu asistente" }),
  ).toBeInTheDocument();
});

it("retains shared database access when automatic personal provisioning fails", async () => {
  const fetch = authenticatedFixture(null, true);
  const original = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (url: string) =>
    url.includes("create_database")
      ? {
          ok: false,
          status: 500,
          json: async () => ({ error: { message: "Preparación fallida" } }),
        }
      : original(url),
  );
  render(<App />);
  await screen.findByText("Preparación fallida");
  expect(screen.getByRole("combobox", { name: "BASE DE DATOS" })).toHaveValue(
    "shared",
  );
  expect(
    screen.getByRole("button", { name: "Reintentar preparación" }),
  ).toBeEnabled();
});

function catalogFixture() {
  const fetch = authenticatedFixture({ onboarding_completed: true });
  const original = fetch.getMockImplementation()!;
  const state = {
    objects: [
      { name: "gastos", kind: "table" },
      { name: "resumen mensual", kind: "view" },
    ].map((object) => ({
      ...object,
      description: "",
      columns: [{ name: "id", type: "bigint", nullable: false }],
      primary_key: ["id"],
      foreign_keys: [],
    })),
  };
  const queries: { database_id: string; sql: string }[] = [];
  fetch.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.includes("catalog"))
      return {
        ok: true,
        json: async () => ({
          result: { fingerprint: "x", objects: state.objects },
        }),
      };
    if (url.includes("/query/")) {
      const payload = JSON.parse(String(init?.body));
      queries.push(payload);
      return {
        ok: true,
        json: async () => ({
          result: {
            columns: ["id"],
            rows: payload.sql.includes('data."gastos"') ? [["Compra"]] : [],
            truncated: false,
          },
        }),
      };
    }
    return original(url);
  });
  return { state, queries };
}

it("switches the query target and resets Explorer using the sidebar", async () => {
  const { queries } = catalogFixture();
  render(<App />);
  await screen.findByText("Compra");
  expect(
    screen.queryByRole("combobox", { name: "Tabla o vista" }),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Filtrar esta página"), {
    target: { value: "ocultar" },
  });
  fireEvent.click(screen.getByRole("tab", { name: "Consulta SQL" }));
  fireEvent.change(screen.getByLabelText("Consulta o cambio de esquema"), {
    target: { value: "SELECT 1" },
  });
  fireEvent.click(screen.getByRole("button", { name: "resumen mensual" }));
  await screen.findByText("No hay registros para mostrar.");
  expect(queries.at(-1)).toEqual({
    database_id: "own",
    sql: 'SELECT * FROM data."resumen mensual" ORDER BY "id" LIMIT 51 OFFSET 0',
  });
  expect(
    screen.getByRole("button", { name: "resumen mensual" }),
  ).toHaveAttribute("aria-current", "true");
  expect(
    screen.getByRole("heading", { name: "resumen mensual" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Datos" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByLabelText("Filtrar esta página")).toHaveValue("");
  expect(screen.queryByText("Compra")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Buscar tablas y vistas"), {
    target: { value: "" },
  });
  expect(
    screen.getByRole("button", { name: "resumen mensual" }),
  ).toHaveAttribute("aria-current", "true");
  fireEvent.click(screen.getByRole("tab", { name: "Consulta SQL" }));
  expect(screen.getByLabelText("Consulta o cambio de esquema")).toHaveValue(
    'SELECT * FROM data."resumen mensual" LIMIT 50;',
  );
  fireEvent.click(screen.getByRole("button", { name: "gastos" }));
  await screen.findByText("Compra");

  expect(screen.getByRole("button", { name: "gastos" })).toHaveAttribute(
    "aria-current",
    "true",
  );
  expect(queries.at(-1)?.sql).toBe(
    'SELECT * FROM data."gastos" ORDER BY "id" LIMIT 51 OFFSET 0',
  );
});

it("keeps selection through refresh and clears it when the catalog becomes empty", async () => {
  const { state, queries } = catalogFixture();
  render(<App />);
  await screen.findByText("Compra");

  fireEvent.click(screen.getByRole("button", { name: "resumen mensual" }));
  await screen.findByText("No hay registros para mostrar.");
  fireEvent.click(screen.getByRole("button", { name: "Actualizar catálogo" }));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Actualizar catálogo" }),
    ).toBeEnabled(),
  );
  expect(
    screen.getByRole("button", { name: "resumen mensual" }),
  ).toHaveAttribute("aria-current", "true");
  state.objects = [];
  const queryCount = queries.length;
  fireEvent.click(screen.getByRole("button", { name: "Actualizar catálogo" }));
  await screen.findByText("Tu esquema empieza acá");
  expect(
    screen.queryByRole("button", { name: "resumen mensual" }),
  ).not.toBeInTheDocument();
  expect(queries).toHaveLength(queryCount);
  fireEvent.click(screen.getByRole("tab", { name: "Consulta SQL" }));
  expect(screen.getByLabelText("Consulta o cambio de esquema")).toHaveValue(
    "SELECT 1;",
  );
});
it("does not recreate a deleted database on reload", async () => {
  const fetch = authenticatedFixture({
    status: "deleted",
    onboarding_completed: false,
  });
  render(<App />);
  await screen.findByRole("heading", { name: "Base eliminada" });
  expect(
    screen.getByRole("button", { name: "Crear base vacía" }),
  ).toBeEnabled();
  expect(
    fetch.mock.calls.some(([url]) => url.includes("create_database")),
  ).toBe(false);
  expect(fetch.mock.calls.some(([url]) => url.includes("/catalog/"))).toBe(
    false,
  );
});

it("searches friendly and physical names without changing the sidebar query target", async () => {
  const { state, queries } = catalogFixture();
  Object.assign(state.objects[0], { display_name: "Gastos personales" });
  render(<App />);
  await screen.findByText("Compra");
  expect(
    screen.getByRole("button", { name: "Gastos personales" }),
  ).toHaveAttribute("aria-current", "true");
  expect(
    screen.getByRole("heading", { name: "Gastos personales" }),
  ).toBeInTheDocument();
  const search = screen.getByLabelText("Buscar tablas y vistas");
  fireEvent.change(search, { target: { value: "PERSONALES" } });
  expect(
    screen.getByRole("button", { name: "Gastos personales" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "resumen mensual" }),
  ).not.toBeInTheDocument();
  fireEvent.change(search, { target: { value: "gastos" } });
  fireEvent.click(screen.getByRole("button", { name: "Gastos personales" }));
  expect(queries.at(-1)?.sql).toContain('data."gastos"');
  expect(
    screen.queryByRole("combobox", { name: "Tabla o vista" }),
  ).not.toBeInTheDocument();
});
