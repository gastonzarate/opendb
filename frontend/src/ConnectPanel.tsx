import { useEffect, useId, useRef, useState } from "react";

type ConnectPanelProps = {
  mcpUrl: string;
  onboarding?: boolean;
  databaseReady?: boolean;
  onComplete?: () => void;
  completing?: boolean;
};

const clients = [
  "Claude Code",
  "Claude Desktop",
  "Kiro",
  "Codex",
  "MCP genérico",
] as const;
type Client = (typeof clients)[number];
// POSIX shell quoting: preserve the supplied endpoint as exactly one argument.
const shellQuote = (value: string) => `'${value.replace(/'/g, `'"'"'`)}'`;
// Página de conectores de Claude (web, Desktop y móvil comparten la misma configuración).
const claudeConnectors = "https://claude.ai/customize/connectors";
const kiroConfig = (url: string) =>
  JSON.stringify({ mcpServers: { opendb: { url, timeout: 120000 } } }, null, 2);
// Deeplink documentado de Cursor: el config va en base64 dentro de la URL.
function cursorDeeplink(url: string) {
  try {
    const config = btoa(JSON.stringify({ url }));
    return `cursor://anysphere.cursor-deeplink/mcp/install?name=opendb&config=${encodeURIComponent(config)}`;
  } catch {
    return "";
  }
}

const isLoopback = (host: string) =>
  host === "localhost" || host === "[::1]" || /^127\./.test(host);

// Public deployments advertise their configured HTTPS endpoint. Loopback is
// usable only when both the app and assistant are being developed locally.
function usableEndpoint(value: string) {
  try {
    const url = new URL(value);
    const localPage = isLoopback(globalThis.location?.hostname || "");
    if (url.username || url.password) return "";
    if (isLoopback(url.hostname) && !localPage) return "";
    return url.protocol === "https:" ||
      (url.protocol === "http:" && localPage && isLoopback(url.hostname))
      ? value
      : "";
  } catch {
    return "";
  }
}

const readPrompt =
  "Usa OpenDB: ejecuta list_databases y muéstrame las bases disponibles. Pregúntame cuál usar si hay varias; después ejecuta catalog con su database_id y describe sus tablas y vistas, o indica si está vacía. No crees ni modifiques datos ni permisos.";
const ingestPrompt =
  'En mi base privada de OpenDB, quiero cargar estos datos de ejemplo: [{"producto":"Cuaderno","cantidad":2},{"producto":"Lápiz","cantidad":3}]. Revisa list_databases y catalog, lee opendb://guides/ingestion, modela estos datos y cárgalos con ingest. No reemplaces datos existentes ni compartas accesos. Después consulta los datos cargados.';

export function ConnectPanel(props: ConnectPanelProps) {
  // Changing endpoint resets copy state and cancels feedback from the old panel.
  return (
    <ConnectionInstructions
      key={props.mcpUrl}
      {...props}
      mcpUrl={usableEndpoint(props.mcpUrl)}
    />
  );
}

function ConnectionInstructions({
  mcpUrl,
  onboarding = false,
  databaseReady = true,
  onComplete,
  completing = false,
}: ConnectPanelProps) {
  const [client, setClient] = useState<Client>("Claude Code");
  const id = useId();
  return (
    <section
      className="panel stack connection-panel"
      aria-label="Conectar un asistente"
    >
      <div className="panel-head">
        <div>
          <h2>
            {onboarding
              ? databaseReady
                ? "Tu base de datos ya está lista"
                : "Preparando tu base de datos"
              : "Conectar un asistente"}
          </h2>
          <p className="muted">
            Conecta tu asistente a tu cuenta de OpenDB para trabajar con tus
            datos.
          </p>
        </div>
      </div>
      {onboarding && (
        <ol>
          <li>
            <strong>Tu espacio privado.</strong>{" "}
            {databaseReady
              ? "Tu base de datos ya está creada; puedes empezar sin configurar tablas."
              : "Tu base de datos aún no está lista. Revisa el panel de estado de la base para seguir la preparación o reintentar si ha fallado. Mientras tanto, puedes configurar tu asistente."}
          </li>
          <li>
            <strong>Describe lo que necesitas.</strong> Tu asistente modela,
            inserta y consulta datos con los permisos de tu cuenta. Aquí puedes
            explorar los resultados.
          </li>
          <li>
            <strong>Tú decides qué compartir.</strong> En Accesos, crea un rol,
            concede tablas o vistas y asigna personas por correo. Las personas
            invitadas solo pueden leer lo que compartas explícitamente.
          </li>
        </ol>
      )}
      <CopyBlock
        label="URL del servidor MCP"
        button="Copiar URL"
        value={mcpUrl}
        url
      />
      {!mcpUrl && (
        <p className="empty">
          La conexión con asistentes todavía no está disponible.
        </p>
      )}
      <div className="tabs" role="tablist" aria-label="Asistente">
        {clients.map((name, index) => (
          <button
            key={name}
            type="button"
            role="tab"
            id={`${id}-tab-${index}`}
            aria-controls={`${id}-panel-${index}`}
            aria-selected={client === name}
            tabIndex={client === name ? 0 : -1}
            onClick={() => setClient(name)}
            onKeyDown={(event) => {
              let next: number;
              if (event.key === "ArrowRight")
                next = (index + 1) % clients.length;
              else if (event.key === "ArrowLeft")
                next = (index + clients.length - 1) % clients.length;
              else if (event.key === "Home") next = 0;
              else if (event.key === "End") next = clients.length - 1;
              else return;
              event.preventDefault();
              setClient(clients[next]);
              document.getElementById(`${id}-tab-${next}`)?.focus();
            }}
          >
            {name}
          </button>
        ))}
      </div>
      <div
        className="stack"
        role="tabpanel"
        id={`${id}-panel-${clients.indexOf(client)}`}
        aria-labelledby={`${id}-tab-${clients.indexOf(client)}`}
        tabIndex={0}
      >
        {client === "Codex" && (
          <>
            <p>
              Con Codex CLI instalado, ejecuta estos comandos en la terminal de
              tu equipo (bash/zsh). Autoriza OpenDB cuando se abra el navegador
              e inicia sesión con tu cuenta de Google.
            </p>
            {mcpUrl && (
              <CopyBlock
                label="Comandos de Codex"
                button="Copiar comandos de Codex"
                value={`codex mcp add opendb --url ${shellQuote(mcpUrl)}\ncodex mcp login opendb`}
              />
            )}
            <p>
              Después abre una sesión de Codex y prueba el acceso con el prompt
              de lectura de abajo.{" "}
              <a href="https://developers.openai.com/codex/mcp/">
                Guía oficial de Codex MCP
              </a>
              .
            </p>
          </>
        )}
        {client === "Claude Code" && (
          <>
            <p>
              1. Agrega OpenDB. Copia este comando y ejecútalo en la terminal
              donde usas Claude Code. Estará disponible en tus proyectos.
            </p>
            {mcpUrl && (
              <CopyBlock
                label="Comando de Claude Code"
                button="Copiar comando de Claude Code"
                value={`claude mcp add --transport http --scope user opendb ${shellQuote(mcpUrl)}`}
              />
            )}
            <p>
              2. Inicia sesión. Abre Claude Code, escribe <code>/mcp</code>,
              selecciona <code>opendb</code> y completa la autenticación OAuth
              en el navegador con la misma cuenta de Google que usas en OpenDB.
            </p>
            <p>
              <a href="https://code.claude.com/docs/en/mcp">
                Guía oficial de Claude Code MCP
              </a>
              .
            </p>
          </>
        )}
        {client === "Claude Desktop" && (
          <>
            <p>
              Claude Desktop (igual que claude.ai y las apps móviles) usa
              conectores personalizados: se configuran una vez en tu cuenta y
              quedan disponibles en todos los clientes de Claude.
            </p>
            <ol>
              <li>
                <strong>Abre los conectores.</strong> En Claude Desktop entra a
                Configuración → Conectores, o usá el botón de abajo para abrir
                la misma página en el navegador.
              </li>
              <li>
                <strong>Agrega un conector personalizado.</strong> Toca “+” →
                “Agregar conector personalizado” y pegá la URL del servidor MCP
                de arriba. En planes Team y Enterprise esto lo hace un Owner
                desde Configuración de la organización → Conectores.
              </li>
              <li>
                <strong>Conectá tu cuenta.</strong> Presioná “Conectar” y
                completá la autorización de OpenDB con la misma cuenta de Google
                que usás acá. Después habilitá el conector en el chat con el
                botón “+”.
              </li>
            </ol>
            <div className="form-row">
              <a
                className="btn btn-primary"
                href={claudeConnectors}
                target="_blank"
                rel="noreferrer noopener"
              >
                Abrir conectores de Claude
              </a>
              {mcpUrl && (
                <span className="small muted">
                  Claude no ofrece un enlace de instalación automática: el botón
                  abre la página y vos pegás la URL copiada.
                </span>
              )}
            </div>
            <p className="small muted">
              Claude se conecta a OpenDB desde la infraestructura de Anthropic,
              no desde tu computadora: la URL tiene que ser accesible por
              internet. Un servidor en localhost o detrás de VPN no funciona,
              incluso con Claude Desktop abierto.{" "}
              <a href="https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp">
                Guía oficial de conectores personalizados
              </a>
              .
            </p>
          </>
        )}
        {client === "Kiro" && (
          <>
            <p>
              1. Agregá OpenDB a Kiro. Pegá esta configuración en{" "}
              <code>~/.kiro/settings/mcp.json</code> (global) o en{" "}
              <code>.kiro/settings/mcp.json</code> dentro del proyecto. Sirve
              igual para Kiro IDE y para Kiro CLI.
            </p>
            {mcpUrl && (
              <CopyBlock
                label="Configuración MCP de Kiro"
                button="Copiar configuración de Kiro"
                value={kiroConfig(mcpUrl)}
                rows={8}
              />
            )}
            <p>
              2. Si preferís la terminal, guardá ese JSON en un archivo e
              importalo:
            </p>
            {mcpUrl && (
              <CopyBlock
                label="Comando de Kiro CLI"
                button="Copiar comando de Kiro"
                value={`kiro-cli mcp import --file opendb-mcp.json global`}
              />
            )}
            <p>
              3. Iniciá sesión. Abrí Kiro, escribí <code>/mcp</code> para ver el
              estado de <code>opendb</code> y completá la autorización OAuth en
              el navegador con tu cuenta de Google.
            </p>
            <p className="small muted">
              Kiro no tiene un enlace de instalación de un clic: la
              configuración se agrega por archivo o con{" "}
              <code>kiro-cli mcp import</code>.
            </p>
          </>
        )}
        {client === "MCP genérico" && (
          <>
            <ol>
              <li>
                Abre las conexiones o herramientas de tu asistente y añade un
                servidor MCP con la URL de arriba.
              </li>
              <li>
                Elige HTTP transmisible (Streamable HTTP) y OAuth. El cliente
                debe admitir ambos.
              </li>
              <li>
                Completa la autorización de OpenDB en el navegador con tu cuenta
                de Google y vuelve al asistente para probar el acceso.
              </li>
            </ol>
            {mcpUrl && (
              <>
                <h3>Instalar con un clic</h3>
                <p>
                  Algunos editores aceptan enlaces de instalación. Si tenés
                  Cursor instalado, este botón abre el diálogo con OpenDB ya
                  cargado.
                </p>
                <div className="form-row">
                  <a
                    className="btn"
                    href={cursorDeeplink(mcpUrl)}
                    aria-label="Instalar OpenDB en Cursor"
                  >
                    Instalar en Cursor
                  </a>
                </div>
                <p>Para VS Code, ejecutá este comando:</p>
                <CopyBlock
                  label="Comando de VS Code"
                  button="Copiar comando de VS Code"
                  value={`code --add-mcp '{"name":"opendb","type":"http","url":"${mcpUrl}"}'`}
                />
              </>
            )}
          </>
        )}
      </div>
      <section className="stack" aria-label="Prueba de lectura">
        <h3>Prueba el acceso sin cambiar datos</h3>
        <p>
          Pega este prompt en tu asistente después de autorizarlo. Comprueba
          allí su respuesta; copiar instrucciones no verifica la conexión.
        </p>
        <CopyBlock
          label="Prompt de prueba de lectura"
          button="Copiar prueba de lectura"
          value={readPrompt}
        />
      </section>
      <details>
        <summary>Carga de ejemplo (opcional)</summary>
        <p>
          Este paso propone crear e insertar datos de ejemplo en tu base
          privada. Úsalo solo si quieres hacer una primera carga.
        </p>
        <CopyBlock
          label="Prompt de carga de ejemplo"
          button="Copiar ejemplo de carga"
          value={ingestPrompt}
        />
      </details>
      {onboarding && (
        <div className="stack">
          <p>
            Al continuar confirmas que has leído esta guía. No se verifica la
            conexión del asistente; puedes configurarlo después.
          </p>
          <button
            type="button"
            className="btn btn-primary"
            disabled={completing || !onComplete}
            aria-busy={completing}
            onClick={onComplete}
          >
            Ir a mis datos
          </button>
        </div>
      )}
    </section>
  );
}

function CopyBlock({
  label,
  button,
  value,
  url = false,
  rows = 3,
}: {
  label: string;
  button: string;
  value: string;
  url?: boolean;
  rows?: number;
}) {
  const [state, setState] = useState<"idle" | "pending" | "copied" | "error">(
    "idle",
  );
  const lifetime = useRef(0);
  const inFlight = useRef(false);
  useEffect(
    () => () => {
      lifetime.current += 1;
    },
    [],
  );
  const copy = async () => {
    if (inFlight.current) return;
    const started = lifetime.current;
    inFlight.current = true;
    setState("pending");
    try {
      await navigator.clipboard.writeText(value);
      if (lifetime.current === started) setState("copied");
    } catch {
      if (lifetime.current === started) setState("error");
    } finally {
      inFlight.current = false;
    }
  };
  return (
    <div className="stack">
      <div className="form-row">
        <label className="field">
          {label}
          {url ? (
            <input
              type="url"
              readOnly
              value={value}
              onFocus={(event) => event.currentTarget.select()}
            />
          ) : (
            <textarea
              className="connect-code"
              readOnly
              rows={rows}
              value={value}
              onFocus={(event) => event.currentTarget.select()}
            />
          )}
        </label>
        <button
          type="button"
          className="btn"
          disabled={state === "pending" || !value}
          onClick={() => void copy()}
        >
          {button}
        </button>
      </div>
      {state === "copied" && (
        <p role="status">{url ? "URL copiada." : "Texto copiado."}</p>
      )}
      {state === "error" && (
        <p className="error" role="alert">
          No se pudo copiar. Selecciona el texto y cópialo manualmente.
        </p>
      )}
    </div>
  );
}
