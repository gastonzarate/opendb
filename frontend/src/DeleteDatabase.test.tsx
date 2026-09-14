import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DeleteDatabase } from "./DeleteDatabase";
const database = { id: "own", is_owner: true, status: "ready" };
afterEach(() => vi.unstubAllGlobals());
function setup() {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
  const done = vi.fn();
  render(<DeleteDatabase database={database} onDeleted={done} />);
  return done;
}
it("requires confirmation before deleting", async () => {
  const fetch = vi.fn(async () => ({
    ok: true,
    json: async () => ({ result: { ...database, status: "deleted" } }),
  }));
  vi.stubGlobal("fetch", fetch);
  const done = setup();
  fireEvent.click(screen.getByRole("button", { name: "Borrar base de datos" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancelar" }));
  expect(fetch).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Borrar base de datos" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Borrar definitivamente" }),
  );
  await waitFor(() =>
    expect(done).toHaveBeenCalledWith({ ...database, status: "deleted" }),
  );
  expect(fetch).toHaveBeenCalledWith(
    "/api/actions/delete_database/",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ database_id: "own" }),
    }),
  );
});
it("allows retry on failure", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network")));
  const done = setup();
  fireEvent.click(screen.getByRole("button", { name: "Borrar base de datos" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Borrar definitivamente" }),
  );
  await screen.findByRole("alert");
  expect(screen.getByRole("dialog")).toBeVisible();
  expect(
    screen.getByRole("button", { name: "Borrar definitivamente" }),
  ).toBeEnabled();
  expect(done).not.toHaveBeenCalled();
});
it("does not offer deletion to guests", () => {
  render(
    <DeleteDatabase
      database={{ ...database, is_owner: false }}
      onDeleted={vi.fn()}
    />,
  );
  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
