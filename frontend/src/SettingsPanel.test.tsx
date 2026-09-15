import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { it, expect, vi, afterEach } from "vitest";
import { SettingsPanel } from "./SettingsPanel";
afterEach(() => vi.unstubAllGlobals());
type Call = { action: string; payload: Record<string, unknown> };
function stubApi(stored: string) {
  const calls: Call[] = [];
  let current = stored;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const action = new URL(url, "http://localhost").pathname
        .split("/")
        .at(-2) as string;
      const payload = JSON.parse(String(init?.body || "{}"));
      calls.push({ action, payload });
      if (action === "update_saving_instructions")
        current = String(payload.instructions);
      return {
        ok: true,
        status: 200,
        json: async () => ({
          result: {
            database_id: "db-1",
            instructions: current,
            max_length: 4000,
            updated_at: "2026-09-15T03:00:00Z",
          },
        }),
      };
    }),
  );
  return calls;
}
it("carga las reglas guardadas y las envía al guardar", async () => {
  const calls = stubApi("Guardá cada reunión.");
  render(<SettingsPanel databaseId="db-1" isOwner />);
  const field = (await screen.findByLabelText(
    "Reglas de guardado",
  )) as HTMLTextAreaElement;
  await waitFor(() => expect(field).toHaveValue("Guardá cada reunión."));
  expect(calls[0]).toEqual({
    action: "saving_instructions",
    payload: { database_id: "db-1" },
  });
  expect(screen.getByRole("button", { name: "Guardar reglas" })).toBeDisabled();
  fireEvent.change(field, { target: { value: "No guardes tarjetas." } });
  const save = screen.getByRole("button", { name: "Guardar reglas" });
  expect(save).toBeEnabled();
  fireEvent.click(save);
  await waitFor(() =>
    expect(calls.at(-1)).toEqual({
      action: "update_saving_instructions",
      payload: { database_id: "db-1", instructions: "No guardes tarjetas." },
    }),
  );
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Guardamos tus reglas",
  );
});
it("agrega una sugerencia sin duplicarla y permite descartar cambios", async () => {
  stubApi("");
  render(<SettingsPanel databaseId="db-1" isOwner />);
  const field = (await screen.findByLabelText(
    "Reglas de guardado",
  )) as HTMLTextAreaElement;
  await waitFor(() => expect(field).toBeEnabled());
  const suggestion = screen.getByRole("button", { name: "Qué no guardar" });
  fireEvent.click(suggestion);
  const added = field.value;
  expect(added).toContain("No guardes borradores");
  fireEvent.click(suggestion);
  expect(field).toHaveValue(added);
  fireEvent.click(screen.getByRole("button", { name: "Descartar cambios" }));
  expect(field).toHaveValue("");
});
it("no muestra el editor a invitados ni consulta la acción", async () => {
  const calls = stubApi("Reglas privadas.");
  render(<SettingsPanel databaseId="db-1" isOwner={false} />);
  expect(
    await screen.findByRole("heading", {
      name: "Solo el dueño de la base configura las reglas",
    }),
  ).toBeInTheDocument();
  expect(screen.queryByLabelText("Reglas de guardado")).not.toBeInTheDocument();
  expect(calls).toEqual([]);
});
it("informa cuando la acción falla y no pierde lo escrito", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => ({
      ok: !url.includes("update_saving_instructions"),
      status: url.includes("update_saving_instructions") ? 400 : 200,
      json: async () =>
        url.includes("update_saving_instructions")
          ? {
              error: { code: "invalid_operation", message: "Texto muy largo." },
            }
          : {
              result: {
                database_id: "db-1",
                instructions: "",
                max_length: 4000,
                updated_at: null,
              },
            },
    })),
  );
  render(<SettingsPanel databaseId="db-1" isOwner />);
  const field = (await screen.findByLabelText(
    "Reglas de guardado",
  )) as HTMLTextAreaElement;
  await waitFor(() => expect(field).toBeEnabled());
  fireEvent.change(field, { target: { value: "Regla nueva." } });
  fireEvent.click(screen.getByRole("button", { name: "Guardar reglas" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Texto muy largo.",
  );
  expect(field).toHaveValue("Regla nueva.");
});
