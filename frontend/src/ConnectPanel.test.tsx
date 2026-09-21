import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConnectPanel } from "./AdminPanels";

afterEach(() => vi.unstubAllGlobals());

describe("ConnectPanel onboarding and setup", () => {
  it("shows preparation and retry guidance until the database is ready", () => {
    const onComplete = vi.fn();
    const view = render(
      <ConnectPanel
        mcpUrl="http://localhost:8001/mcp"
        onboarding
        databaseReady={false}
        onComplete={onComplete}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Preparando tu base de datos" }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/ya está creada/)).toBeNull();
    expect(screen.getByText(/panel de estado/)).toHaveTextContent(
      /reintentar/i,
    );
    expect(onComplete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Ir a mis datos" }));
    expect(onComplete).toHaveBeenCalledTimes(1);
    view.rerender(
      <ConnectPanel
        mcpUrl="http://localhost:8001/mcp"
        onboarding
        databaseReady
        onComplete={onComplete}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Tu base de datos ya está lista" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/ya está creada/)).toBeInTheDocument();
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("only acknowledges onboarding through the manual CTA", async () => {
    const onComplete = vi.fn();
    vi.stubGlobal("navigator", {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
    const view = render(
      <ConnectPanel
        mcpUrl="http://localhost:8000/mcp/"
        onboarding
        onComplete={onComplete}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Tu base de datos ya está lista" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Copiar URL" }));
    expect(await screen.findByRole("status")).toHaveTextContent("URL copiada");
    expect(onComplete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Ir a mis datos" }));
    expect(onComplete).toHaveBeenCalledTimes(1);
    view.rerender(
      <ConnectPanel
        mcpUrl="http://localhost:8000/mcp/"
        onboarding
        onComplete={onComplete}
        completing
      />,
    );
    expect(
      screen.getByRole("button", { name: "Ir a mis datos" }),
    ).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Ir a mis datos" }));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("switches client instructions and copies shell-quoted commands without altering the endpoint", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(
      <ConnectPanel
        mcpUrl={"https://datos.example/custom/mcp?x='$(touch nope)&y=`id`"}
      />,
    );
    expect(screen.queryByRole("button", { name: "Ir a mis datos" })).toBeNull();
    expect(screen.getByRole("tab", { name: "Claude Code" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Codex" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Copiar comandos de Codex" }),
    );
    await screen.findByRole("status");
    expect(writeText).toHaveBeenLastCalledWith(
      "codex mcp add opendb --url 'https://datos.example/custom/mcp?x='\"'\"'$(touch nope)&y=`id`'\ncodex mcp login opendb",
    );
    fireEvent.click(screen.getByRole("tab", { name: "Claude Code" }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("/mcp");
    fireEvent.click(
      screen.getByRole("button", { name: "Copiar comando de Claude Code" }),
    );
    await screen.findByRole("status");
    expect(writeText).toHaveBeenLastCalledWith(
      "claude mcp add --transport http --scope user opendb 'https://datos.example/custom/mcp?x='\"'\"'$(touch nope)&y=`id`'",
    );
    fireEvent.click(screen.getByRole("tab", { name: "MCP genérico" }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Streamable HTTP");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("OAuth");
  });

  it("guides Kiro with a config file, an import command and no invented deep link", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<ConnectPanel mcpUrl="https://datos.example/mcp/" />);
    fireEvent.click(screen.getByRole("tab", { name: "Kiro" }));
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("~/.kiro/settings/mcp.json");
    expect(panel).toHaveTextContent("/mcp");
    expect(screen.getByLabelText("Configuración MCP de Kiro")).toHaveValue(
      JSON.stringify(
        {
          mcpServers: {
            opendb: { url: "https://datos.example/mcp/", timeout: 120000 },
          },
        },
        null,
        2,
      ),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Copiar comando de Kiro" }),
    );
    await screen.findByRole("status");
    expect(writeText).toHaveBeenLastCalledWith(
      "kiro-cli mcp import --file opendb-mcp.json global",
    );
  });

  it("explains Claude Desktop connectors and links to the real settings page", () => {
    render(<ConnectPanel mcpUrl="https://datos.example/mcp/" />);
    fireEvent.click(screen.getByRole("tab", { name: "Claude Desktop" }));
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("conector personalizado");
    expect(panel).toHaveTextContent("infraestructura de Anthropic");
    expect(
      screen.getByRole("link", { name: "Abrir conectores de Claude" }),
    ).toHaveAttribute("href", "https://claude.ai/customize/connectors");
  });

  it("offers a Cursor install link built from the endpoint", () => {
    render(<ConnectPanel mcpUrl="https://datos.example/mcp/" />);
    fireEvent.click(screen.getByRole("tab", { name: "MCP genérico" }));
    const link = screen.getByRole("link", {
      name: "Instalar OpenDB en Cursor",
    });
    const href = link.getAttribute("href") || "";
    expect(href).toMatch(
      /^cursor:\/\/anysphere\.cursor-deeplink\/mcp\/install\?name=opendb&config=/,
    );
    const config = decodeURIComponent(href.split("config=")[1]);
    expect(JSON.parse(atob(config))).toEqual({
      url: "https://datos.example/mcp/",
    });
  });

  it("keeps the read-only trial separate from optional ingestion", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    vi.stubGlobal("navigator", { clipboard: { writeText } });
    render(<ConnectPanel mcpUrl="https://datos.example/mcp/" />);
    fireEvent.click(
      screen.getByRole("button", { name: "Copiar prueba de lectura" }),
    );
    await screen.findByRole("status");
    const prompt = writeText.mock.calls[0][0];
    expect(prompt).toMatch(/list_databases/);
    expect(prompt).toMatch(/catalog/);
    expect(prompt).toMatch(/No crees ni modifiques/);
    expect(prompt).not.toMatch(/ingest/);
    fireEvent.click(screen.getByText("Carga de ejemplo (opcional)"));
    fireEvent.click(
      screen.getByRole("button", { name: "Copiar ejemplo de carga" }),
    );
    await screen.findByRole("status");
    expect(writeText.mock.calls[1][0]).toMatch(/ingest/);
    expect(writeText.mock.calls[1][0]).not.toMatch(
      /pregúntame antes de escribir/i,
    );
  });

  it("does not generate commands for a missing URL", () => {
    render(<ConnectPanel mcpUrl="" />);
    expect(screen.getByRole("button", { name: "Copiar URL" })).toBeDisabled();
    expect(
      screen.queryByRole("button", { name: "Copiar comandos de Codex" }),
    ).toBeNull();
  });

  it("does not offer a development endpoint or SSH steps on the public web", () => {
    vi.stubGlobal("location", { hostname: "app.example.com" });
    render(<ConnectPanel mcpUrl="http://localhost:8001/mcp" />);
    expect(
      screen.getByText(
        "La conexión con asistentes todavía no está disponible.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Comando de Claude Code")).toBeNull();
    expect(
      screen.queryByText(/SSH|Tailscale|Google Console|localhost/),
    ).toBeNull();
  });

  it("connects to the configured HTTPS domain without infrastructure steps", () => {
    vi.stubGlobal("location", { hostname: "app.example.com" });
    render(<ConnectPanel mcpUrl="https://mcp.example.com/mcp" />);
    expect(screen.getByLabelText("Comando de Claude Code")).toHaveValue(
      "claude mcp add --transport http --scope user opendb 'https://mcp.example.com/mcp'",
    );
    expect(screen.getByRole("tabpanel")).toHaveTextContent("/mcp");
    expect(
      screen.queryByText(/SSH|Tailscale|Google Console|localhost/),
    ).toBeNull();
  });

  it("offers manual copy when clipboard is unavailable", async () => {
    vi.stubGlobal("navigator", {});
    render(<ConnectPanel mcpUrl="https://datos.example/mcp/" />);
    fireEvent.click(screen.getByRole("button", { name: "Copiar URL" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/manualmente/);
    expect(screen.getByLabelText("URL del servidor MCP")).toHaveValue(
      "https://datos.example/mcp/",
    );
  });

  it("hides the personal access token panel unless local login is enabled", () => {
    render(<ConnectPanel mcpUrl="https://datos.example/mcp/" />);
    expect(
      screen.queryByText("Alternativa local sin Google"),
    ).not.toBeInTheDocument();
  });

  it("creates and revokes a personal access token when local login is enabled", async () => {
    let tokens: unknown[] = [];
    const fetch = vi.fn(async (url: string) => {
      if (url.endsWith("/api/personal-access-tokens/"))
        return { ok: true, json: async () => ({ tokens }) };
      if (url.endsWith("/api/personal-access-tokens/create/")) {
        const token = {
          id: 1,
          name: "laptop",
          prefix: "odbpat_abc123",
          created_at: "2026-01-01T00:00:00Z",
          last_used_at: null,
          revoked_at: null,
        };
        tokens = [token];
        return {
          ok: true,
          json: async () => ({ token, raw_token: "odbpat_abc123secret" }),
        };
      }
      if (url.endsWith("/1/revoke/")) {
        return { ok: true, json: async () => ({ result: "revoked" }) };
      }
      return { ok: true, json: async () => ({ tokens }) };
    });
    vi.stubGlobal("fetch", fetch);
    render(
      <ConnectPanel mcpUrl="https://datos.example/mcp/" localLoginEnabled />,
    );
    await screen.findByText("Alternativa local sin Google");
    fireEvent.click(screen.getByRole("button", { name: "Generar token" }));
    expect(
      await screen.findByLabelText("Token generado (se muestra una sola vez)"),
    ).toHaveValue("odbpat_abc123secret");
    expect(screen.getByText("odbpat_abc123…")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revocar token" }));
    await screen.findByText("Revocado");
  });

  it("discards copy feedback from a previous endpoint", async () => {
    let finish!: () => void;
    vi.stubGlobal("navigator", {
      clipboard: {
        writeText: () =>
          new Promise<void>((resolve) => {
            finish = resolve;
          }),
      },
    });
    const view = render(<ConnectPanel mcpUrl="https://old.example/mcp/" />);
    fireEvent.click(screen.getByRole("button", { name: "Copiar URL" }));
    view.rerender(<ConnectPanel mcpUrl="https://new.example/mcp/" />);
    await act(async () => finish());
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByLabelText("URL del servidor MCP")).toHaveValue(
      "https://new.example/mcp/",
    );
  });
});
