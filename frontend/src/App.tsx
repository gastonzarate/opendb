import { useEffect, useRef, useState } from "react";
import {
  Database as DatabaseIcon,
  Table2,
  Layers,
  Users,
  Network,
  Plug,
  Settings,
  ArrowUpRight,
  Search,
  LogOut,
  ShieldCheck,
  ChevronRight,
  Menu,
  RefreshCw,
} from "lucide-react";
import { api, read, setCsrf, ApiError } from "./api";
import { asset, monkeyMask } from "./assets";
import type { Bootstrap, Catalog, Database, Session } from "./types";
import { DeleteDatabase } from "./DeleteDatabase";
import { Explorer } from "./Explorer";
import { AccessPanel, VectorPanel, ConnectPanel } from "./AdminPanels";
import { SettingsPanel } from "./SettingsPanel";
const headings: Record<string, { title: string; lead: string }> = {
  connect: {
    title: "Conectá tu asistente",
    lead: "Usá tu información desde el cliente que prefieras.",
  },
  explorer: {
    title: "Mis datos",
    lead: "Revisá lo que tu asistente guardó. Elegí una tabla o vista para explorar sus datos.",
  },
  access: {
    title: "Compartí con control",
    lead: "Elegí quién puede consultar cada parte de tu base.",
  },
  vectors: {
    title: "Contexto listo para buscar",
    lead: "Seguí el estado de la búsqueda semántica.",
  },
  settings: {
    title: "Configuración",
    lead: "Definí cuándo y cómo tu asistente guarda información en tu base.",
  },
};
const Brand = () => (
  <span className="brand">
    <span className="brand-mark">
      <DatabaseIcon size={21} />
    </span>
    open<span className="brand-light">db</span>
    <span className="beta">BETA</span>
  </span>
);
function Login({ boot }: { boot: Bootstrap }) {
  return (
    <div className="login">
      <div
        className="login-jungle"
        aria-hidden="true"
        style={{
          backgroundImage: `url("${asset("wall-alt-chimp-down.jpg")}")`,
        }}
      />
      <div className="login-nav">
        <Brand />
        <span className="muted">Tu contexto. Bajo tu control.</span>
      </div>
      <main className="login-main">
        <div className="login-copy">
          <div className="pill">
            <span className="status-dot" />
            UN ESPACIO PARA TU CONTEXTO
          </div>
          <h1>
            Tu información,
            <br />
            lista para <em>conectar.</em>
          </h1>
          <p>
            Una base de datos personal para guardar lo que importa, consultarlo
            con tu asistente y compartir solo lo que vos elegís.
          </p>
          <div className="login-features">
            <span>
              <DatabaseIcon size={19} />
              Una base propia
            </span>
            <span>
              <Network size={19} />
              Búsqueda inteligente
            </span>
            <span>
              <ShieldCheck size={19} />
              Accesos que controlás
            </span>
          </div>
        </div>
        <section className="login-card">
          <span
            className="monkey login-monkey"
            style={monkeyMask("macaco-6.png")}
            aria-hidden="true"
          />
          <span className="large-mark">
            <DatabaseIcon size={30} />
          </span>
          <h2>Bienvenido a OpenDB</h2>
          <p className="muted">Entrá a tu espacio de trabajo.</p>
          <form method="post" action="/accounts/google/login/">
            <input
              type="hidden"
              name="csrfmiddlewaretoken"
              value={boot.csrf_token}
            />
            <input type="hidden" name="next" value="/app/" />
            <button
              className="btn google-btn"
              disabled={!boot.google_configured}
            >
              <span className="google-g" aria-hidden="true">
                G
              </span>
              Continuar con Google
              <ArrowUpRight size={17} />
            </button>
          </form>
          {!boot.google_configured && (
            <p role="alert" className="error">
              Google todavía no está configurado en este servidor.
            </p>
          )}
          <div className="login-divider" />
          <p className="small muted">
            <ShieldCheck size={15} /> Tus credenciales de Google nunca se
            comparten con tu asistente.
          </p>
          <div className="login-footnote">
            Datos estructurados. Contexto compartido.
          </div>
        </section>
      </main>
      <footer className="login-footer">
        OpenDB / Tu base de datos personal
        <span>Construido para conectar con tu asistente.</span>
      </footer>
    </div>
  );
}
export default function App() {
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const b = await read<Bootstrap>("/api/bootstrap/");
        if (!alive) return;
        setBoot(b);
        setCsrf(b.csrf_token);
        try {
          const s = await read<Session>("/api/session/");
          if (alive) {
            setSession(s);
            setCsrf(s.csrf_token);
          }
        } catch (e) {
          if (!(e instanceof ApiError && e.status === 401)) throw e;
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        if (alive) setReady(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);
  if (error)
    return (
      <div className="startup-error">
        <h1>No pudimos conectar con OpenDB</h1>
        <p role="alert">{error}</p>
        <button className="btn" onClick={() => location.reload()}>
          Reintentar
        </button>
      </div>
    );
  if (!ready || !boot)
    return (
      <div className="startup-error" role="status">
        <span className="spinner" />
        Abriendo tu espacio…
      </div>
    );
  return session ? (
    <Workspace boot={boot} session={session} />
  ) : (
    <Login boot={boot} />
  );
}
function Workspace({ boot, session }: { boot: Bootstrap; session: Session }) {
  const [databases, setDatabases] = useState<Database[]>([]);
  const [selected, setSelected] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const started = useRef(false);
  async function load() {
    setLoading(true);
    setError("");
    try {
      let dbs = await api<Database[]>("list_databases");
      setDatabases(dbs);
      setSelected((old) =>
        dbs.some((d) => d.id === old)
          ? old
          : (dbs.find((d) => d.is_owner) || dbs[0])?.id || "",
      );
      if (!dbs.some((d) => d.is_owner)) {
        setCreating(true);
        await api<Database>("create_database");
        dbs = await api<Database[]>("list_databases");
        setSelected(dbs.find((d) => d.is_owner)?.id || "");
      }
      setDatabases(dbs);
      setSelected((old) =>
        dbs.some((d) => d.id === old)
          ? old
          : (dbs.find((d) => d.is_owner) || dbs[0])?.id || "",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
      setCreating(false);
    }
  }
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void load();
  }, []);
  async function create() {
    setCreating(true);
    setError("");
    try {
      const db = await api<Database>("create_database");
      await load();
      setSelected(db.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCreating(false);
    }
  }
  const db = databases.find((d) => d.id === selected);
  return (
    <div className="workspace">
      <header className="topbar">
        <Brand />
        <div className="workspace-label">
          <span className="separator" />
          Espacio personal
        </div>
        <div className="topbar-right">
          <span className="user-email">{session.user.email}</span>
          <span className="avatar">
            {session.user.email.slice(0, 1).toUpperCase()}
          </span>
          <form method="post" action="/accounts/logout/">
            <input
              type="hidden"
              name="csrfmiddlewaretoken"
              value={session.csrf_token}
            />
            <input type="hidden" name="next" value="/app/" />
            <button
              title="Cerrar sesión"
              aria-label="Cerrar sesión"
              className="icon-btn"
            >
              <LogOut size={16} />
            </button>
          </form>
        </div>
      </header>
      {loading && !db ? (
        <div className="startup-error" role="status">
          Preparando tu base personal…
        </div>
      ) : (
        <DatabaseWorkspace
          key={`${db?.id || "empty"}:${db?.status || ""}`}
          db={db}
          onDeleted={(deleted) =>
            setDatabases((current) =>
              current.map((item) => (item.id === deleted.id ? deleted : item)),
            )
          }
          databases={databases}
          select={setSelected}
          create={create}
          creating={creating}
          missingOwn={!databases.some((item) => item.is_owner)}
          boot={boot}
          onOnboardingComplete={() =>
            setDatabases((current) =>
              current.map((item) =>
                item.id === db?.id
                  ? { ...item, onboarding_completed: true }
                  : item,
              ),
            )
          }
          error={error}
        />
      )}
    </div>
  );
}
function DatabaseWorkspace({
  db,
  databases,
  select,
  create,
  creating,
  missingOwn,
  boot,
  error: outerError,
  onOnboardingComplete,
  onDeleted,
}: {
  db?: Database;
  databases: Database[];
  select: (id: string) => void;
  create: () => void;
  creating: boolean;
  missingOwn: boolean;
  boot: Bootstrap;
  error: string;
  onOnboardingComplete: () => void;
  onDeleted: (db: Database) => void;
}) {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [objectName, setObjectName] = useState("");
  const [onboarding, setOnboarding] = useState(
    Boolean(db?.is_owner && !db.onboarding_completed),
  );
  const [section, setSection] = useState(
    db?.is_owner && !db.onboarding_completed ? "connect" : "explorer",
  );
  const [completing, setCompleting] = useState(false);
  async function completeOnboarding() {
    if (!db) return;
    setCompleting(true);
    setError("");
    try {
      await api("complete_onboarding", { database_id: db.id });
      onOnboardingComplete();
      setOnboarding(false);
      setSection("explorer");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCompleting(false);
    }
  }
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [mobile, setMobile] = useState(false);
  async function refresh() {
    if (!db || db.status !== "ready") return;
    setLoading(true);
    setError("");
    try {
      const result = await api<Catalog>("catalog", { database_id: db.id });
      setCatalog(result);
      setObjectName((old) =>
        result.objects.some((o) => o.name === old)
          ? old
          : result.objects[0]?.name || "",
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    let alive = true;
    if (db?.status === "ready") {
      setLoading(true);
      api<Catalog>("catalog", { database_id: db.id })
        .then((result) => {
          if (alive) {
            setCatalog(result);
            setObjectName(result.objects[0]?.name || "");
          }
        })
        .catch((e) => {
          if (alive) setError(e.message);
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }
    return () => {
      alive = false;
    };
  }, [db?.id, db?.status]);
  const objects = catalog?.objects || [];
  const chosen = objects.find((o) => o.name === objectName) || null;
  const nav = [
    { id: "connect", label: "Conectar asistente", icon: Plug },
    { id: "explorer", label: "Mis datos", icon: Table2 },
    { id: "access", label: "Accesos", icon: Users },
    { id: "vectors", label: "Indexación", icon: Network },
    { id: "settings", label: "Configuración", icon: Settings },
  ];
  return (
    <div className="workspace-body">
      <button
        className="mobile-toggle btn"
        onClick={() => setMobile(!mobile)}
        aria-label="Abrir navegación"
      >
        <Menu size={18} />
        Navegación
      </button>
      <aside className={`sidebar ${mobile ? "is-open" : ""}`}>
        <div className="db-selector">
          <label htmlFor="database">BASE DE DATOS</label>
          <select
            id="database"
            value={db?.id || ""}
            onChange={(e) => select(e.target.value)}
          >
            {!databases.length && <option value="">Sin base creada</option>}
            {databases.map((d, i) => (
              <option key={d.id} value={d.id}>
                {d.is_owner
                  ? "Mi base de datos"
                  : `Compartida · ${d.id.slice(0, 8)}`}
              </option>
            ))}
          </select>
          <span className="db-status">
            <span
              className={`status-dot ${db?.status !== "ready" ? "offline" : ""}`}
            />
            {db?.status === "ready"
              ? "Disponible"
              : db
                ? db.status === "deleted"
                  ? "Eliminada"
                  : db.status === "delete_failed"
                    ? "Borrado pendiente"
                    : "Pendiente de aprovisionar"
                : "Lista para empezar"}
          </span>
        </div>
        <nav>
          {nav
            .filter((n) => n.id !== "access" || db?.is_owner)
            .map(({ id, label, icon: Icon }) => (
              <button
                className={section === id ? "nav-active" : ""}
                key={id}
                onClick={() => {
                  setSection(id);
                  setMobile(false);
                }}
              >
                <Icon size={17} />
                {label}
                {id === "explorer" && (
                  <span className="nav-count">{objects.length}</span>
                )}
              </button>
            ))}
        </nav>
        {section === "explorer" && (
          <>
            <div className="objects-heading">
              TABLAS Y VISTAS<span>{objects.length}</span>
            </div>
            <label className="sidebar-search">
              <Search size={14} />
              <input
                aria-label="Buscar tablas y vistas"
                placeholder="Buscar tabla o vista…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <div className="object-list">
              {objects
                .filter((o) =>
                  [o.name, o.display_name || ""].some((name) =>
                    name
                      .toLocaleLowerCase()
                      .includes(query.toLocaleLowerCase()),
                  ),
                )
                .map((o) => (
                  <button
                    key={o.name}
                    title={o.name}
                    className={
                      section === "explorer" && o.name === objectName
                        ? "object-active"
                        : ""
                    }
                    onClick={() => {
                      setObjectName(o.name);
                      setSection("explorer");
                      setMobile(false);
                    }}
                    aria-current={
                      section === "explorer" && o.name === objectName
                        ? "true"
                        : undefined
                    }
                  >
                    {o.kind === "view" ? (
                      <Layers size={15} />
                    ) : (
                      <Table2 size={15} />
                    )}
                    <span>{o.display_name?.trim() || o.name}</span>
                  </button>
                ))}
              {!objects.length && (
                <p className="sidebar-hint">
                  <span
                    className="monkey monkey-badge"
                    style={monkeyMask("macaco-4.png")}
                    aria-hidden="true"
                  />
                  Tus tablas y vistas
                  <br />
                  aparecerán acá.
                </p>
              )}
            </div>
          </>
        )}
        {db?.is_owner && db.status !== "deleted" && (
          <DeleteDatabase
            database={db}
            onDeleted={onDeleted}
            onUnavailable={() => onDeleted({ ...db, status: "delete_failed" })}
          />
        )}
        <div className="sidebar-bottom">
          <ShieldCheck size={18} />
          <div>
            {db?.is_owner ? "Tu base. Tus permisos." : "Acceso compartido"}
            <small>
              {db?.is_owner
                ? "Compartí solo lo que elegís."
                : "Solo información autorizada."}
            </small>
          </div>
        </div>
      </aside>
      <main className="main-content">
        <div className="breadcrumbs">
          Espacio personal
          <ChevronRight size={14} />
          {nav.find((n) => n.id === section)?.label}
        </div>
        <div className="page-heading">
          <div>
            <div className="eyebrow">TU ESPACIO DE CONTEXTO</div>
            <h1>{headings[section]?.title || headings.connect.title}</h1>
            <p className="muted">
              {headings[section]?.lead || headings.connect.lead}
            </p>
          </div>
          {db?.status === "ready" && section === "explorer" && (
            <button className="btn" onClick={refresh} disabled={loading}>
              <RefreshCw size={15} />
              Actualizar catálogo
            </button>
          )}
        </div>
        {(error || outerError) && (
          <div className="error" role="alert">
            {error || outerError}
          </div>
        )}
        {db && missingOwn && (
          <div className="panel">
            <p>
              Podés consultar las bases compartidas mientras preparamos tu base
              personal.
            </p>
            <button className="btn" onClick={create} disabled={creating}>
              {creating ? "Preparando tu base…" : "Reintentar preparación"}
            </button>
          </div>
        )}
        {!db ? (
          <section className="panel welcome">
            <img
              className="welcome-monkey"
              src={asset("chimp-shades.png")}
              alt=""
              aria-hidden="true"
            />
            <h2>Estamos preparando tu base personal</h2>
            <p className="muted">
              Tu cuenta incluye una única base privada. Si hubo un problema al
              prepararla, podés reintentar.
            </p>
            <button
              className="btn btn-primary"
              onClick={create}
              disabled={creating}
            >
              {creating ? "Preparando tu base…" : "Reintentar preparación"}
            </button>
          </section>
        ) : ["deleted", "deleting", "delete_failed"].includes(db.status) ? (
          <section className="panel empty">
            <span
              className="monkey monkey-illus"
              style={monkeyMask("macaco-5.png")}
              aria-hidden="true"
            />
            <h2>
              {db.status === "deleted"
                ? "Base eliminada"
                : "El borrado todavía no terminó"}
            </h2>
            <p className="muted">
              {db.status === "deleted"
                ? "Tu cuenta sigue activa. Podés crear una base vacía cuando quieras."
                : "Tus datos no están disponibles. Reintentá el borrado para completarlo."}
            </p>
            {db.is_owner && db.status === "deleted" && (
              <button
                className="btn btn-primary"
                onClick={create}
                disabled={creating}
              >
                {creating ? "Creando…" : "Crear base vacía"}
              </button>
            )}
          </section>
        ) : section === "connect" ? (
          <>
            {db.status !== "ready" && db.is_owner && (
              <div className="panel">
                <p role="status">
                  Tu base todavía no está lista. Podés configurar el asistente y
                  reintentar la preparación.
                </p>
                <button className="btn" onClick={create} disabled={creating}>
                  Reintentar preparación
                </button>
              </div>
            )}
            <ConnectPanel
              mcpUrl={boot.mcp_url}
              databaseReady={db.status === "ready"}
              onboarding={onboarding}
              onComplete={() => void completeOnboarding()}
              completing={completing}
            />
          </>
        ) : section === "settings" ? (
          <SettingsPanel databaseId={db.id} isOwner={db.is_owner} />
        ) : db?.status !== "ready" ? (
          <section className="panel empty">
            <span
              className="monkey monkey-illus"
              style={monkeyMask("macaco-3.png")}
              aria-hidden="true"
            />
            <h2>La base todavía no está lista</h2>
            {db?.is_owner && (
              <button className="btn" onClick={create} disabled={creating}>
                Reintentar aprovisionamiento
              </button>
            )}
          </section>
        ) : (
          <>
            {section === "explorer" &&
              (loading && !catalog ? (
                <section className="panel empty" role="status">
                  Cargando catálogo…
                </section>
              ) : (
                <>
                  <Explorer
                    key={objectName}
                    databaseId={db.id}
                    object={chosen}
                    isOwner={db.is_owner}
                    onChanged={refresh}
                  />
                </>
              ))}
            {section === "access" && (
              <AccessPanel
                databaseId={db.id}
                objects={objects}
                isOwner={db.is_owner}
              />
            )}
            {section === "vectors" && (
              <VectorPanel
                databaseId={db.id}
                objects={objects}
                isOwner={db.is_owner}
              />
            )}
          </>
        )}
        <footer className="workspace-footer">
          <span>
            <span className="status-dot" />
            OpenDB · Beta local
          </span>
          <span>Una base propia. Infinitas posibilidades.</span>
        </footer>
      </main>
    </div>
  );
}
