import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { it, expect, vi, beforeEach } from "vitest";
import { Explorer } from "./Explorer";
import { api } from "./api";
vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  api: vi.fn(),
}));
const obj = {
  name: "expenses",
  kind: "table",
  description: "Gastos",
  primary_key: ["id"],
  columns: [{ name: "id", type: "integer", nullable: false }],
};
// Returning the mock registers it as a cleanup callback, causing an extra API call.
beforeEach(() => {
  vi.mocked(api).mockReset();
});
it("renders real results and paginates", async () => {
  vi.mocked(api).mockResolvedValue({
    columns: ["id"],
    rows: Array.from({ length: 51 }, (_, i) => [i]),
    truncated: false,
  });
  render(
    <Explorer
      databaseId="db"
      object={obj}
      isOwner={false}
      onChanged={() => {}}
    />,
  );
  await screen.findByText(/^50 filas/);
  fireEvent.click(screen.getByRole("button", { name: "Página siguiente" }));
  await waitFor(() =>
    expect(api).toHaveBeenLastCalledWith(
      "query",
      expect.objectContaining({ sql: expect.stringContaining("OFFSET 50") }),
    ),
  );
});
it("shows failures instead of an empty successful table", async () => {
  vi.mocked(api).mockRejectedValue(new Error("Sin permiso"));
  render(
    <Explorer
      databaseId="db"
      object={obj}
      isOwner={false}
      onChanged={() => {}}
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("Sin permiso");
});

it("switching away from a pending data request leaves SQL usable", async () => {
  let resolve!: (r: unknown) => void;
  vi.mocked(api).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }) as never,
  );
  render(
    <Explorer databaseId="db" object={obj} isOwner onChanged={() => {}} />,
  );
  fireEvent.click(screen.getByRole("tab", { name: "Consulta SQL" }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Ejecutar" })).toBeEnabled(),
  );
  resolve({ columns: ["id"], rows: [[99]] });
});

it("shows friendly metadata once while querying and editing the physical name", async () => {
  vi.mocked(api).mockResolvedValue({ columns: ["id"], rows: [] });
  const object = {
    ...obj,
    display_name: "Gastos personales",
    attributes_summary: "Identificador y monto de cada gasto.",
  };
  render(
    <Explorer databaseId="db" object={object} isOwner onChanged={() => {}} />,
  );
  expect(
    screen.getByRole("heading", { name: "Gastos personales" }),
  ).toBeInTheDocument();
  expect(screen.getByText(/data\.expenses$/)).toBeInTheDocument();
  expect(screen.getAllByText("Gastos")).toHaveLength(1);
  expect(screen.getAllByText(object.attributes_summary)).toHaveLength(1);
  await waitFor(() =>
    expect(api).toHaveBeenCalledWith("query", {
      database_id: "db",
      sql: 'SELECT * FROM data."expenses" ORDER BY "id" LIMIT 51 OFFSET 0',
    }),
  );
  fireEvent.click(screen.getByRole("tab", { name: "Estructura" }));
  expect(screen.getAllByText(object.attributes_summary)).toHaveLength(1);
  fireEvent.click(screen.getByRole("tab", { name: "Consulta SQL" }));
  expect(screen.getByLabelText("Consulta o cambio de esquema")).toHaveValue(
    'SELECT * FROM data."expenses" LIMIT 50;',
  );
});

it("keeps legacy table names without empty or duplicate metadata", async () => {
  vi.mocked(api).mockResolvedValue({ columns: [], rows: [] });
  render(
    <Explorer databaseId="db" object={obj} isOwner onChanged={() => {}} />,
  );
  expect(screen.getByRole("heading", { name: "expenses" })).toBeInTheDocument();
  expect(screen.queryByText("data.expenses")).not.toBeInTheDocument();
  await screen.findByText("No hay registros para mostrar.");
});

it("omits repeated summaries and falls back from blank display names", async () => {
  vi.mocked(api).mockResolvedValue({ columns: [], rows: [] });
  const object = { ...obj, display_name: "  ", attributes_summary: " Gastos " };
  render(
    <Explorer databaseId="db" object={object} isOwner onChanged={() => {}} />,
  );
  expect(screen.getByRole("heading", { name: "expenses" })).toBeInTheDocument();
  expect(screen.getAllByText("Gastos")).toHaveLength(1);
  await screen.findByText("No hay registros para mostrar.");
});
