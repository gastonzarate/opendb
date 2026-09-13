// @vitest-environment jsdom
import { StrictMode } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import type { DataObject } from "./types";
import { AccessPanel, ConnectPanel, VectorPanel } from "./AdminPanels";

vi.mock("./api", () => ({ api: vi.fn() }));
const request = vi.mocked(api);
const objects: DataObject[] = [
  {
    name: "articulos",
    kind: "table",
    description: "",
    primary_key: ["codigo"],
    columns: [
      { name: "codigo", type: "uuid", nullable: false },
      { name: "contenido", type: "text", nullable: true },
    ],
  },
];
const props = { databaseId: "db-1", objects, isOwner: true };
const role = {
  id: "role-1",
  name: "Lectores",
  objects: ["articulos"],
  emails: ["ana@example.com"],
};
const index = {
  index_id: "idx-1",
  table: "data.articulos",
  key_column: "codigo",
  text_column: "contenido",
  model_id: "modelo",
  view_name: "vector_1",
  pending: 3,
  processing: 2,
  ready: 7,
  failed: 1,
};
beforeEach(() => {
  request.mockReset();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("AccessPanel", () => {
  it("muestra roles reales y revoca objetos y personas con sus identificadores", async () => {
    request.mockResolvedValueOnce([role]);
    render(<AccessPanel {...props} />);
    expect(screen.getByRole("status").textContent).toMatch(/cargando/i);
    expect(
      await screen.findByRole("heading", { name: "Lectores" }),
    ).toBeTruthy();
    expect(request).toHaveBeenCalledWith("list_access", {
      database_id: "db-1",
    });
    request
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce([{ ...role, objects: [] }]);
    fireEvent.click(
      screen.getByRole("button", {
        name: "Revocar acceso a articulos del rol Lectores",
      }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", {
          name: "Revocar acceso a articulos del rol Lectores",
        }),
      ).toBeNull(),
    );
    expect(request).toHaveBeenCalledWith("revoke_object", {
      role_id: "role-1",
      object_name: "articulos",
    });
    request
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce([{ ...role, objects: [], emails: [] }]);
    fireEvent.click(
      screen.getByRole("button", {
        name: "Quitar a ana@example.com del rol Lectores",
      }),
    );
    await waitFor(() =>
      expect(screen.queryByText("ana@example.com")).toBeNull(),
    );
    expect(request).toHaveBeenCalledWith("revoke_role", {
      role_id: "role-1",
      email: "ana@example.com",
    });
  });

  it("crea un rol, concede un objeto e invita por correo, recargando el estado real", async () => {
    request.mockResolvedValueOnce([]);
    render(<AccessPanel {...props} />);
    await screen.findByText(/no hay roles/i);
    request
      .mockResolvedValueOnce({ id: "role-1", name: "Lectores" })
      .mockResolvedValueOnce([{ ...role, objects: [], emails: [] }]);
    fireEvent.change(screen.getByLabelText("Nombre del rol"), {
      target: { value: " Lectores " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Crear rol" }));
    await screen.findByRole("heading", { name: "Lectores" });
    expect(request).toHaveBeenCalledWith("create_role", {
      database_id: "db-1",
      name: "Lectores",
    });
    request
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce([{ ...role, emails: [] }]);
    fireEvent.change(screen.getByLabelText("Objeto para Lectores"), {
      target: { value: "articulos" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Conceder acceso" }));
    await screen.findByRole("button", {
      name: "Revocar acceso a articulos del rol Lectores",
    });
    expect(request).toHaveBeenCalledWith("grant_object", {
      role_id: "role-1",
      object_name: "articulos",
    });
    request.mockResolvedValueOnce(null).mockResolvedValueOnce([role]);
    fireEvent.change(screen.getByLabelText("Correo para Lectores"), {
      target: { value: "ana@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Invitar" }));
    await screen.findByText("ana@example.com");
    expect(request).toHaveBeenCalledWith("assign_role", {
      role_id: "role-1",
      email: "ana@example.com",
    });
  });

  it("no solicita ni expone administración para invitados", () => {
    render(<AccessPanel {...props} isOwner={false} />);
    expect(screen.getByText(/solo el propietario/i)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Crear rol" })).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });

  it("permite reintentar una carga fallida sin mostrar datos inventados", async () => {
    request.mockRejectedValueOnce(new Error("Sin conexión"));
    render(<AccessPanel {...props} />);
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /Sin conexión/,
    );
    request.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Actualizar accesos" }));
    await screen.findByText(/no hay roles/i);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("conserva un permiso cuando falla su revocación", async () => {
    request.mockResolvedValueOnce([role]);
    render(<AccessPanel {...props} />);
    await screen.findByText("ana@example.com");
    request.mockRejectedValueOnce(new Error("No se pudo revocar"));
    fireEvent.click(
      screen.getByRole("button", {
        name: "Quitar a ana@example.com del rol Lectores",
      }),
    );
    await screen.findByRole("alert");
    expect(screen.getByText("ana@example.com")).toBeTruthy();
  });

  it("no inicia la recarga de una mutación que finaliza después de desmontarse", async () => {
    request.mockResolvedValueOnce([]);
    const view = render(<AccessPanel {...props} />);
    await screen.findByText(/no hay roles/i);
    let finish!: (value: unknown) => void;
    request.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finish = resolve;
        }),
    );
    fireEvent.change(screen.getByLabelText("Nombre del rol"), {
      target: { value: "Lectores" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Crear rol" }));
    view.unmount();
    await act(async () => finish({ id: "role-1", name: "Lectores" }));
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("ignora una respuesta obsoleta durante la repetición de efectos de StrictMode", async () => {
    let finish!: (value: unknown) => void;
    request
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      )
      .mockResolvedValueOnce([role]);
    render(
      <StrictMode>
        <AccessPanel {...props} />
      </StrictMode>,
    );
    await screen.findByRole("heading", { name: "Lectores" });
    await act(async () => finish([{ ...role, name: "Rol obsoleto" }]));
    expect(screen.queryByRole("heading", { name: "Rol obsoleto" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Lectores" })).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Actualizar accesos" }),
    ).not.toBeDisabled();
  });
});

describe("VectorPanel", () => {
  it("registra usando las columnas del catálogo y muestra contadores y resultados reales", async () => {
    request.mockResolvedValueOnce([]);
    render(<VectorPanel {...props} />);
    await screen.findByText(/no hay índices/i);
    fireEvent.change(screen.getByLabelText("Tabla de origen"), {
      target: { value: "articulos" },
    });
    expect(
      (screen.getByLabelText("Columna clave") as HTMLSelectElement).value,
    ).toBe("codigo");
    expect(
      (screen.getByLabelText("Columna de texto") as HTMLSelectElement).value,
    ).toBe("contenido");
    request
      .mockResolvedValueOnce({ index_id: "idx-1", view_name: "vector_1" })
      .mockResolvedValueOnce([index]);
    fireEvent.click(screen.getByRole("button", { name: "Registrar índice" }));
    await screen.findByRole("heading", { name: "data.articulos · contenido" });
    expect(request).toHaveBeenCalledWith("register_vector", {
      database_id: "db-1",
      table: "articulos",
      key_column: "codigo",
      text_column: "contenido",
    });
    expect(screen.getByLabelText("Pendientes").textContent).toContain("3");
    expect(screen.getByLabelText("Listos").textContent).toContain("7");
    request.mockResolvedValueOnce([
      {
        index_id: "idx-1",
        source_key: "abc",
        version: 1,
        chunk_order: 0,
        char_start: 0,
        char_end: 19,
        token_start: 0,
        token_end: 4,
        text: "Un hallazgo preciso",
        score: 0.87,
      },
    ]);
    fireEvent.change(screen.getByLabelText("Consulta semántica"), {
      target: { value: "hallazgo" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await screen.findByText("Un hallazgo preciso");
    expect(request).toHaveBeenCalledWith("search_vectors", {
      database_id: "db-1",
      index_id: "idx-1",
      query: "hallazgo",
      limit: 10,
    });
    expect(screen.getByText(/0.870/)).toBeTruthy();
  });

  it("excluye vistas, claves compuestas y tablas sin texto", async () => {
    request.mockResolvedValueOnce([]);
    render(
      <VectorPanel
        {...props}
        objects={[
          ...objects,
          { ...objects[0], name: "vista", kind: "view" },
          {
            ...objects[0],
            name: "compuesta",
            primary_key: ["codigo", "contenido"],
          },
          {
            ...objects[0],
            name: "sin_texto",
            columns: [objects[0].columns[0]],
          },
        ]}
      />,
    );
    await screen.findByText(/no hay índices/i);
    const select = screen.getByLabelText("Tabla de origen");
    expect(within(select).queryByRole("option", { name: "vista" })).toBeNull();
    expect(
      within(select).queryByRole("option", { name: "compuesta" }),
    ).toBeNull();
    expect(
      within(select).queryByRole("option", { name: "sin_texto" }),
    ).toBeNull();
  });

  it("permite consultar a invitados sin ofrecer registro y recupera errores de estado", async () => {
    request.mockRejectedValueOnce(new Error("Servicio no disponible"));
    render(<VectorPanel {...props} isOwner={false} />);
    await screen.findByRole("alert");
    expect(
      screen.queryByRole("button", { name: "Registrar índice" }),
    ).toBeNull();
    request.mockResolvedValueOnce([index]);
    fireEvent.click(
      screen.getByRole("button", { name: "Actualizar vectores" }),
    );
    await screen.findByRole("heading", { name: "data.articulos · contenido" });
    expect(screen.getByRole("button", { name: "Buscar" })).toBeTruthy();
  });

  it("no inventa un índice cuando falla el registro y conserva las columnas para reintentar", async () => {
    request.mockResolvedValueOnce([]);
    render(<VectorPanel {...props} />);
    await screen.findByText(/no hay índices/i);
    fireEvent.change(screen.getByLabelText("Tabla de origen"), {
      target: { value: "articulos" },
    });
    request.mockRejectedValueOnce(new Error("La tabla no es compatible"));
    fireEvent.click(screen.getByRole("button", { name: "Registrar índice" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /La tabla no es compatible/,
    );
    expect(screen.getByText(/no hay índices/i)).toBeTruthy();
    expect(
      (screen.getByLabelText("Columna de texto") as HTMLSelectElement).value,
    ).toBe("contenido");
    expect(
      screen.getByRole("button", { name: "Registrar índice" }),
    ).not.toBeDisabled();
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("retira resultados anteriores al cambiar la consulta y permite reintentar una búsqueda fallida", async () => {
    request.mockResolvedValueOnce([index]);
    render(<VectorPanel {...props} />);
    await screen.findByLabelText("Consulta semántica");
    request.mockResolvedValueOnce([
      {
        index_id: "idx-1",
        source_key: "abc",
        version: 1,
        chunk_order: 0,
        char_start: 0,
        char_end: 16,
        token_start: 0,
        token_end: 3,
        text: "Resultado previo",
        score: 0.9,
      },
    ]);
    fireEvent.change(screen.getByLabelText("Consulta semántica"), {
      target: { value: "primera consulta" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await screen.findByText("Resultado previo");
    fireEvent.change(screen.getByLabelText("Consulta semántica"), {
      target: { value: "otra consulta" },
    });
    expect(screen.queryByText("Resultado previo")).toBeNull();
    request.mockRejectedValueOnce(new Error("Modelo no disponible"));
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await screen.findByRole("alert");
    request.mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", { name: "Buscar" }));
    await screen.findByText(/no se encontraron resultados/i);
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("ConnectPanel", () => {
  it("copia exactamente la URL proporcionada y confirma el resultado", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<ConnectPanel mcpUrl="https://datos.example/mcp/" />);
    fireEvent.click(screen.getByRole("button", { name: "Copiar URL" }));
    expect((await screen.findByRole("status")).textContent).toMatch(/copiada/i);
    expect(writeText).toHaveBeenCalledWith("https://datos.example/mcp/");
  });

  it("ofrece copia manual si el portapapeles falla", async () => {
    vi.stubGlobal("navigator", {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    });
    render(<ConnectPanel mcpUrl="https://datos.example/mcp/" />);
    fireEvent.click(screen.getByRole("button", { name: "Copiar URL" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(
      /manualmente/i,
    );
    expect(
      (screen.getByLabelText("URL del servidor MCP") as HTMLInputElement).value,
    ).toBe("https://datos.example/mcp/");
  });
});
