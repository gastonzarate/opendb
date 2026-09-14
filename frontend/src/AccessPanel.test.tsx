// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AccessPanel } from "./AdminPanels";
import { api } from "./api";
vi.mock("./api", () => ({ api: vi.fn() }));
const request = vi.mocked(api);
const roles = [
  {
    id: "r1",
    name: "Ventas",
    description: "Analiza ventas",
    objects: [],
    emails: [],
  },
  {
    id: "r2",
    name: "Soporte",
    description: "Consulta incidencias",
    objects: [],
    emails: [],
  },
];
const props = { databaseId: "db1", objects: [], isOwner: true };
beforeEach(() => request.mockReset());
afterEach(cleanup);
it("muestra solo el detalle del rol seleccionado", async () => {
  request.mockResolvedValueOnce(roles);
  render(<AccessPanel {...props} />);
  fireEvent.click(
    await screen.findByRole("button", { name: "Seleccionar rol Soporte" }),
  );
  expect(screen.queryByRole("region", { name: "Rol Ventas" })).toBeNull();
  expect(screen.getByLabelText("Descripción para Soporte")).toHaveValue(
    "Consulta incidencias",
  );
});
it("crea nombre y descripción sin permisos automáticos y selecciona el nuevo rol", async () => {
  request.mockResolvedValueOnce([roles[0]]);
  render(<AccessPanel {...props} />);
  await screen.findByRole("heading", { name: "Ventas" });
  fireEvent.change(screen.getByLabelText("Nombre del rol"), {
    target: { value: " Soporte " },
  });
  fireEvent.change(screen.getByLabelText("Descripción del rol"), {
    target: { value: " Consulta incidencias " },
  });
  request.mockResolvedValueOnce({ id: "r2" }).mockResolvedValueOnce(roles);
  fireEvent.click(screen.getByRole("button", { name: "Crear rol" }));
  await screen.findByRole("region", { name: "Rol Soporte" });
  expect(request).toHaveBeenCalledWith("create_role", {
    database_id: "db1",
    name: "Soporte",
    description: "Consulta incidencias",
  });
  expect(request.mock.calls.map(([action]) => action)).toEqual([
    "list_access",
    "create_role",
    "list_access",
  ]);
  expect(screen.getByText(/no concede permisos automáticamente/i)).toBeTruthy();
});
it("permite vaciar la descripción sin cambiar permisos", async () => {
  request.mockResolvedValueOnce(roles);
  render(<AccessPanel {...props} />);
  const input = await screen.findByLabelText("Descripción para Ventas");
  fireEvent.change(input, { target: { value: "" } });
  request
    .mockResolvedValueOnce(null)
    .mockResolvedValueOnce([{ ...roles[0], description: "" }, roles[1]]);
  fireEvent.click(
    screen.getByRole("button", { name: "Guardar cambios del rol" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Guardar cambios del rol" }),
    ).not.toBeDisabled(),
  );
  expect(screen.getByLabelText("Descripción para Ventas")).toHaveValue("");
  expect(request).toHaveBeenCalledWith("update_role", {
    database_id: "db1",
    role_id: "r1",
    name: "Ventas",
    description: "",
  });
  expect(request.mock.calls.map(([action]) => action)).toEqual([
    "list_access",
    "update_role",
    "list_access",
  ]);
});
it("conserva la descripción escrita si falla el guardado", async () => {
  request.mockResolvedValueOnce(roles);
  render(<AccessPanel {...props} />);
  const input = await screen.findByLabelText("Descripción para Ventas");
  fireEvent.change(input, { target: { value: "Nueva descripción" } });
  request.mockRejectedValueOnce(new Error("Sin conexión"));
  fireEvent.click(
    screen.getByRole("button", { name: "Guardar cambios del rol" }),
  );
  await screen.findByRole("alert");
  expect(input).toHaveValue("Nueva descripción");
});
it("rechaza descripciones de más de 2000 caracteres y permite corregirlas", async () => {
  request.mockResolvedValueOnce(roles);
  render(<AccessPanel {...props} />);
  const input = await screen.findByLabelText("Descripción para Ventas");
  fireEvent.change(input, { target: { value: "a".repeat(2001) } });
  fireEvent.click(
    screen.getByRole("button", { name: "Guardar cambios del rol" }),
  );
  expect((await screen.findByRole("alert")).textContent).toMatch(/2000/);
  expect(request).toHaveBeenCalledTimes(1);
  request
    .mockResolvedValueOnce(null)
    .mockResolvedValueOnce([{ ...roles[0], description: "a".repeat(2000) }]);
  fireEvent.change(input, { target: { value: "a".repeat(2000) } });
  fireEvent.click(
    screen.getByRole("button", { name: "Guardar cambios del rol" }),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Guardar cambios del rol" }),
    ).not.toBeDisabled(),
  );
  expect(screen.queryByRole("alert")).toBeNull();
  expect(request).toHaveBeenCalledWith("update_role", {
    database_id: "db1",
    role_id: "r1",
    name: "Ventas",
    description: "a".repeat(2000),
  });
});
it("concede una vista e invita a una persona dentro del rol seleccionado", async () => {
  request.mockResolvedValueOnce(roles);
  render(
    <AccessPanel
      {...props}
      objects={[
        {
          name: "resumen",
          kind: "view",
          description: "",
          primary_key: [],
          columns: [],
        },
        {
          name: "interno",
          kind: "index",
          description: "",
          primary_key: [],
          columns: [],
        },
      ]}
    />,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Seleccionar rol Soporte" }),
  );
  expect(screen.queryByRole("option", { name: "interno" })).toBeNull();
  request
    .mockResolvedValueOnce(null)
    .mockResolvedValueOnce([roles[0], { ...roles[1], objects: ["resumen"] }]);
  fireEvent.change(screen.getByLabelText("Objeto para Soporte"), {
    target: { value: "resumen" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Conceder acceso" }));
  await screen.findByRole("button", {
    name: "Revocar acceso a resumen del rol Soporte",
  });
  expect(request).toHaveBeenCalledWith("grant_object", {
    role_id: "r2",
    object_name: "resumen",
  });
  expect(screen.queryByRole("option", { name: "resumen" })).toBeNull();
  request
    .mockResolvedValueOnce(null)
    .mockResolvedValueOnce([
      roles[0],
      { ...roles[1], objects: ["resumen"], emails: ["ana@example.com"] },
    ]);
  fireEvent.change(screen.getByLabelText("Correo para Soporte"), {
    target: { value: "ana@example.com" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Invitar" }));
  await screen.findByText("ana@example.com");
  expect(request).toHaveBeenCalledWith("assign_role", {
    role_id: "r2",
    email: "ana@example.com",
  });
  expect(
    screen.getByRole("button", { name: "Seleccionar rol Soporte" }),
  ).toHaveAttribute("aria-pressed", "true");
});
it("edita nombre y descripción conservando permisos y personas del rol", async () => {
  const existing = {
    ...roles[0],
    objects: ["resumen"],
    emails: ["ana@example.com"],
  };
  request.mockResolvedValueOnce([existing, roles[1]]);
  render(<AccessPanel {...props} />);
  const name = await screen.findByLabelText("Nombre del rol seleccionado");
  expect(name).toHaveValue("Ventas");
  fireEvent.change(name, { target: { value: " Ventas regionales " } });
  fireEvent.change(screen.getByLabelText("Descripción para Ventas"), {
    target: { value: " Consulta ventas regionales " },
  });
  request.mockResolvedValueOnce(null).mockResolvedValueOnce([
    {
      ...existing,
      name: "Ventas regionales",
      description: "Consulta ventas regionales",
    },
    roles[1],
  ]);
  fireEvent.click(
    screen.getByRole("button", { name: "Guardar cambios del rol" }),
  );
  await screen.findByRole("heading", { name: "Ventas regionales" });
  expect(request).toHaveBeenCalledWith("update_role", {
    database_id: "db1",
    role_id: "r1",
    name: "Ventas regionales",
    description: "Consulta ventas regionales",
  });
  expect(screen.getByLabelText("Nombre del rol seleccionado")).toHaveValue(
    "Ventas regionales",
  );
  expect(
    screen.getByRole("button", { name: "Seleccionar rol Ventas regionales" }),
  ).toHaveAttribute("aria-pressed", "true");
  expect(
    screen.getByRole("button", {
      name: "Revocar acceso a resumen del rol Ventas regionales",
    }),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", {
      name: "Quitar a ana@example.com del rol Ventas regionales",
    }),
  ).toBeTruthy();
  expect(request.mock.calls.map(([action]) => action)).toEqual([
    "list_access",
    "update_role",
    "list_access",
  ]);
});
it("no envía un nombre del rol seleccionado compuesto solo por espacios", async () => {
  request.mockResolvedValueOnce(roles);
  render(<AccessPanel {...props} />);
  fireEvent.change(
    await screen.findByLabelText("Nombre del rol seleccionado"),
    { target: { value: "   " } },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Guardar cambios del rol" }),
  );
  expect(request).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("heading", { name: "Ventas" })).toBeTruthy();
});
