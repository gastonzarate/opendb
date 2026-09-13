import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";
import type { DataObject } from "./types";

type PanelProps = {
  databaseId: string;
  objects: DataObject[];
  isOwner: boolean;
};
type AccessRole = {
  id: string;
  name: string;
  objects: string[];
  emails: string[];
};
type VectorIndex = {
  index_id: string;
  table: string;
  key_column: string;
  text_column: string;
  model_id: string;
  view_name: string;
  pending: number;
  processing: number;
  ready: number;
  failed: number;
};
type SearchResult = {
  source_key: unknown;
  chunk_order: number;
  text: string;
  score: number;
};
type Current = () => boolean;

// Each request belongs to one mounted lifetime, including StrictMode's effect replay.
function useRequest() {
  const lifetime = useRef(0);
  const inFlight = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(
    () => () => {
      lifetime.current += 1;
      inFlight.current = false;
    },
    [],
  );
  const run = useCallback(async (task: (current: Current) => Promise<void>) => {
    if (inFlight.current) return;
    const started = lifetime.current;
    const current = () => lifetime.current === started;
    inFlight.current = true;
    setPending(true);
    setError("");
    try {
      await task(current);
    } catch (cause) {
      if (current())
        setError(
          cause instanceof Error
            ? cause.message
            : "No se pudo completar la operación. Inténtalo de nuevo.",
        );
    } finally {
      if (current()) {
        inFlight.current = false;
        setPending(false);
      }
    }
  }, []);
  return { pending, error, run };
}

function RequestError({ error }: { error: string }) {
  return error ? (
    <p className="error" role="alert">
      No se pudo completar la solicitud: {error}
    </p>
  ) : null;
}

export function AccessPanel({ databaseId, objects, isOwner }: PanelProps) {
  // Keep owner-only state in a child so changing ownership also cancels pending work.
  return isOwner ? (
    <OwnerAccess databaseId={databaseId} objects={objects} />
  ) : (
    <section className="panel" aria-label="Accesos">
      <h2>Accesos</h2>
      <p className="empty">
        Solo el propietario puede administrar los roles y permisos de esta base
        de datos.
      </p>
    </section>
  );
}

function OwnerAccess({ databaseId, objects }: Omit<PanelProps, "isOwner">) {
  const [roles, setRoles] = useState<AccessRole[] | null>(null);
  const { pending, error, run } = useRequest();
  const refresh = useCallback(
    () =>
      run(async (current) => {
        const result = await api<AccessRole[]>("list_access", {
          database_id: databaseId,
        });
        if (current()) setRoles(result);
      }),
    [databaseId, run],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const mutate = (
    action: string,
    payload: Record<string, unknown>,
    form?: HTMLFormElement,
  ) => {
    void run(async (current) => {
      await api(action, payload);
      if (!current()) return;
      // Mutations may return null or partial records. Always read authoritative roles.
      const result = await api<AccessRole[]>("list_access", {
        database_id: databaseId,
      });
      if (current()) {
        setRoles(result);
        form?.reset();
      }
    });
  };
  const create = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const name = String(new FormData(form).get("name") ?? "").trim();
    if (name) mutate("create_role", { database_id: databaseId, name }, form);
  };

  return (
    <section className="panel stack" aria-label="Accesos">
      <div className="panel-head">
        <div>
          <h2>Accesos</h2>
          <p className="muted">
            Comparte tablas y vistas mediante roles de lectura.
          </p>
        </div>
        <button
          className="btn"
          disabled={pending}
          onClick={() => void refresh()}
        >
          Actualizar accesos
        </button>
      </div>
      <RequestError error={error} />
      {pending && (
        <p role="status">
          {roles === null ? "Cargando accesos…" : "Actualizando accesos…"}
        </p>
      )}
      <form className="form-row" onSubmit={create}>
        <label className="field">
          Nombre del rol
          <input name="name" required maxLength={100} disabled={pending} />
        </label>
        <button className="btn btn-primary" disabled={pending}>
          Crear rol
        </button>
      </form>
      {roles?.length === 0 && (
        <p className="empty">
          No hay roles. Crea uno para compartir el acceso.
        </p>
      )}
      <div className="stack">
        {roles?.map((role) => {
          const available = objects.filter(
            (object) =>
              ["table", "view"].includes(object.kind) &&
              !role.objects.includes(object.name),
          );
          return (
            <section
              className="panel stack"
              key={role.id}
              aria-label={`Rol ${role.name}`}
            >
              <h3>{role.name}</h3>
              <div>
                <h4>Objetos compartidos</h4>
                {role.objects.length === 0 && (
                  <p className="muted">Sin objetos compartidos.</p>
                )}
                {role.objects.map((name) => (
                  <span className="chip" key={name}>
                    {name}{" "}
                    <button
                      className="btn btn-small"
                      disabled={pending}
                      aria-label={`Revocar acceso a ${name} del rol ${role.name}`}
                      onClick={() =>
                        mutate("revoke_object", {
                          role_id: role.id,
                          object_name: name,
                        })
                      }
                    >
                      Quitar
                    </button>
                  </span>
                ))}
              </div>
              <form
                className="form-row"
                onSubmit={(event) => {
                  event.preventDefault();
                  const form = event.currentTarget;
                  const object = String(new FormData(form).get("object") ?? "");
                  if (available.some((item) => item.name === object))
                    mutate(
                      "grant_object",
                      { role_id: role.id, object_name: object },
                      form,
                    );
                }}
              >
                <label className="field">
                  Objeto para {role.name}
                  <select
                    name="object"
                    required
                    defaultValue=""
                    disabled={pending || available.length === 0}
                  >
                    <option value="">
                      {available.length
                        ? "Selecciona una tabla o vista"
                        : "No hay objetos disponibles"}
                    </option>
                    {available.map((object) => (
                      <option key={object.name} value={object.name}>
                        {object.name}
                      </option>
                    ))}
                  </select>
                </label>
                <button
                  className="btn"
                  disabled={pending || available.length === 0}
                >
                  Conceder acceso
                </button>
              </form>
              <div>
                <h4>Personas</h4>
                {role.emails.length === 0 && (
                  <p className="muted">Sin personas asignadas.</p>
                )}
                {role.emails.map((email) => (
                  <span className="chip" key={email}>
                    {email}{" "}
                    <button
                      className="btn btn-small"
                      disabled={pending}
                      aria-label={`Quitar a ${email} del rol ${role.name}`}
                      onClick={() =>
                        mutate("revoke_role", { role_id: role.id, email })
                      }
                    >
                      Quitar
                    </button>
                  </span>
                ))}
              </div>
              <form
                className="form-row"
                onSubmit={(event) => {
                  event.preventDefault();
                  const form = event.currentTarget;
                  const email = String(
                    new FormData(form).get("email") ?? "",
                  ).trim();
                  if (email)
                    mutate("assign_role", { role_id: role.id, email }, form);
                }}
              >
                <label className="field">
                  Correo para {role.name}
                  <input
                    name="email"
                    type="email"
                    required
                    disabled={pending}
                  />
                </label>
                <button className="btn" disabled={pending}>
                  Invitar
                </button>
              </form>
              <p className="muted">
                La persona obtiene acceso al iniciar sesión con ese correo. Esta
                acción no envía un mensaje.
              </p>
            </section>
          );
        })}
      </div>
    </section>
  );
}

const textType = (type: string) =>
  /^(text|character varying|varchar|character|char|bpchar)(\(\d+\))?$/.test(
    type,
  );
const keyType = (type: string) =>
  textType(type) ||
  ["smallint", "integer", "bigint", "int2", "int4", "int8", "uuid"].includes(
    type,
  );
const counters = [
  ["pending", "Pendientes"],
  ["processing", "En proceso"],
  ["ready", "Listos"],
  ["failed", "Fallidos"],
] as const;

export function VectorPanel({ databaseId, objects, isOwner }: PanelProps) {
  const [indexes, setIndexes] = useState<VectorIndex[] | null>(null);
  const [tableName, setTableName] = useState("");
  const [textColumn, setTextColumn] = useState("");
  const [indexId, setIndexId] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const { pending, error, run } = useRequest();
  // The catalog proves single-column primary keys. Other unique indexes and table
  // restrictions remain backend validation; never guess a key named "id".
  const eligible = objects.filter(
    (object) =>
      object.kind === "table" &&
      !("row_security" in object && object.row_security) &&
      object.primary_key.length === 1 &&
      object.columns.some(
        (column) =>
          column.name === object.primary_key[0] &&
          !column.nullable &&
          keyType(column.type),
      ) &&
      object.columns.some((column) => textType(column.type)),
  );
  const table = eligible.find((object) => object.name === tableName);
  const textColumns =
    table?.columns.filter((column) => textType(column.type)) ?? [];
  const selectedText = textColumns.some((column) => column.name === textColumn)
    ? textColumn
    : (textColumns[0]?.name ?? "");
  const selectedIndex =
    indexes?.find((index) => index.index_id === indexId)?.index_id ??
    indexes?.[0]?.index_id ??
    "";
  const refresh = useCallback(
    () =>
      run(async (current) => {
        setResults(null);
        const result = await api<VectorIndex[]>("vector_status", {
          database_id: databaseId,
        });
        if (current()) setIndexes(result);
      }),
    [databaseId, run],
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section className="panel stack" aria-label="Vectores">
      <div className="panel-head">
        <div>
          <h2>Vectores</h2>
          <p className="muted">
            Indexa texto y busca por significado en los datos que puedes
            consultar.
          </p>
        </div>
        <button
          className="btn"
          disabled={pending}
          onClick={() => void refresh()}
        >
          Actualizar vectores
        </button>
      </div>
      <RequestError error={error} />
      {pending && (
        <p role="status">
          {indexes === null ? "Cargando vectores…" : "Procesando solicitud…"}
        </p>
      )}
      {isOwner && (
        <section className="stack" aria-label="Registro de índices">
          <h3>Registrar texto</h3>
          <p className="muted">
            Selecciona una tabla con clave primaria simple de tipo entero, UUID
            o texto. El servidor comprueba que sea compatible antes de
            registrarla.
          </p>
          {eligible.length === 0 ? (
            <p className="empty">
              No hay tablas compatibles con clave primaria simple y una columna
              de texto.
            </p>
          ) : (
            <form
              className="form-row"
              onSubmit={(event) => {
                event.preventDefault();
                if (!isOwner || !table || !selectedText) return;
                void run(async (current) => {
                  await api("register_vector", {
                    database_id: databaseId,
                    table: table.name,
                    key_column: table.primary_key[0],
                    text_column: selectedText,
                  });
                  if (!current()) return;
                  const result = await api<VectorIndex[]>("vector_status", {
                    database_id: databaseId,
                  });
                  if (current()) {
                    setIndexes(result);
                    setResults(null);
                  }
                });
              }}
            >
              <label className="field">
                Tabla de origen
                <select
                  required
                  value={table?.name ?? ""}
                  disabled={pending}
                  onChange={(event) => {
                    setTableName(event.target.value);
                    setTextColumn("");
                  }}
                >
                  <option value="">Selecciona una tabla</option>
                  {eligible.map((object) => (
                    <option key={object.name} value={object.name}>
                      {object.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Columna clave
                <select value={table?.primary_key[0] ?? ""} disabled>
                  {table ? (
                    <option value={table.primary_key[0]}>
                      {table.primary_key[0]}
                    </option>
                  ) : (
                    <option value="">Selecciona una tabla</option>
                  )}
                </select>
              </label>
              <label className="field">
                Columna de texto
                <select
                  required
                  disabled={pending || !table}
                  value={selectedText}
                  onChange={(event) => setTextColumn(event.target.value)}
                >
                  {!table && <option value="">Selecciona una tabla</option>}
                  {textColumns.map((column) => (
                    <option key={column.name} value={column.name}>
                      {column.name}
                    </option>
                  ))}
                </select>
              </label>
              <button
                className="btn btn-primary"
                disabled={pending || !table || !selectedText}
              >
                Registrar índice
              </button>
            </form>
          )}
          <p className="muted">
            El texto se procesa en segundo plano. Actualiza el estado para ver
            el progreso.
          </p>
        </section>
      )}
      {!isOwner && (
        <p className="muted">
          Solo el propietario puede registrar índices. Aquí se muestran los que
          tienes permiso de consultar.
        </p>
      )}
      {indexes?.length === 0 && (
        <p className="empty">
          No hay índices vectoriales visibles en esta base de datos.
        </p>
      )}
      <div className="stack">
        {indexes?.map((index) => (
          <section className="panel" key={index.index_id}>
            <h3>
              {index.table} · {index.text_column}
            </h3>
            <p className="muted">
              Clave: {index.key_column} · Modelo: {index.model_id} · Vista:{" "}
              {index.view_name}
            </p>
            <div className="card-grid">
              {counters.map(([key, label]) => (
                <div className="stat" aria-label={label} key={key}>
                  <strong>{index[key]}</strong>
                  <span>{label}</span>
                </div>
              ))}
            </div>
            {index.failed > 0 && (
              <p className="muted">
                Hay filas con errores de indexación. El estado refleja los
                intentos del procesamiento en segundo plano.
              </p>
            )}
          </section>
        ))}
      </div>
      {!!indexes?.length && (
        <section className="stack" aria-label="Búsqueda semántica">
          <h3>Búsqueda semántica</h3>
          <form
            className="form-row"
            onSubmit={(event) => {
              event.preventDefault();
              if (!selectedIndex || !query.trim()) return;
              void run(async (current) => {
                setResults(null);
                const result = await api<SearchResult[]>("search_vectors", {
                  database_id: databaseId,
                  index_id: selectedIndex,
                  query: query.trim(),
                  limit: 10,
                });
                if (current()) setResults(result);
              });
            }}
          >
            <label className="field">
              Índice
              <select
                value={selectedIndex}
                disabled={pending}
                onChange={(event) => {
                  setIndexId(event.target.value);
                  setResults(null);
                }}
              >
                {indexes.map((index) => (
                  <option key={index.index_id} value={index.index_id}>
                    {index.table} · {index.text_column}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Consulta semántica
              <input
                required
                value={query}
                disabled={pending}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setResults(null);
                }}
              />
            </label>
            <button
              className="btn btn-primary"
              disabled={pending || !query.trim()}
            >
              Buscar
            </button>
          </form>
          {results?.length === 0 && (
            <p className="empty">
              No se encontraron resultados. Comprueba si hay filas listas o
              prueba otra consulta.
            </p>
          )}
          {results !== null && results.length > 0 && (
            <div className="stack" aria-label="Resultados de búsqueda">
              <p role="status">Resultados: {results.length}</p>
              {results.map((result, position) => (
                <article className="panel" key={position}>
                  <p>{result.text}</p>
                  <p className="muted">
                    Clave:{" "}
                    {typeof result.source_key === "string"
                      ? result.source_key
                      : JSON.stringify(result.source_key)}{" "}
                    · Fragmento: {result.chunk_order + 1} · Similitud:{" "}
                    {result.score.toFixed(3)}
                  </p>
                </article>
              ))}
            </div>
          )}
        </section>
      )}
    </section>
  );
}

export function ConnectPanel({ mcpUrl }: { mcpUrl: string }) {
  const [copied, setCopied] = useState(false);
  const { pending, error, run } = useRequest();
  useEffect(() => {
    setCopied(false);
  }, [mcpUrl]);
  return (
    <section className="panel stack" aria-label="Conectar un asistente">
      <div className="panel-head">
        <div>
          <h2>Conectar un asistente</h2>
          <p className="muted">
            Usa el servidor MCP para trabajar con tus datos desde un asistente
            compatible.
          </p>
        </div>
      </div>
      <div className="form-row">
        <label className="field">
          URL del servidor MCP
          <input
            type="url"
            readOnly
            value={mcpUrl}
            onFocus={(event) => event.currentTarget.select()}
          />
        </label>
        <button
          className="btn btn-primary"
          disabled={pending || !mcpUrl}
          onClick={() => {
            setCopied(false);
            void run(async (current) => {
              try {
                if (!navigator.clipboard?.writeText)
                  throw new Error("Portapapeles no disponible");
                await navigator.clipboard.writeText(mcpUrl);
              } catch {
                throw new Error(
                  "No se pudo copiar la URL. Selecciónala y cópiala manualmente.",
                );
              }
              if (current()) setCopied(true);
            });
          }}
        >
          {pending ? "Copiando…" : "Copiar URL"}
        </button>
      </div>
      {!mcpUrl && (
        <p className="empty">
          La URL del servidor MCP todavía no está disponible.
        </p>
      )}
      <RequestError error={error} />
      {copied && <p role="status">URL copiada.</p>}
      <ol>
        <li>
          Abre la configuración de conexiones o herramientas de tu asistente y
          añade un servidor MCP remoto.
        </li>
        <li>
          Pega esta URL y elige el transporte HTTP transmisible (Streamable
          HTTP) y la autenticación OAuth si el cliente lo solicita.
        </li>
        <li>
          Inicia sesión en OpenDB y autoriza la conexión cuando se abra la
          pantalla de autenticación.
        </li>
        <li>
          Pide al asistente que liste tus bases de datos y describa las tablas y
          vistas disponibles. Indica con cuál quieres trabajar.
        </li>
      </ol>
      <p className="muted">
        El asistente utiliza los permisos de tu cuenta. Las personas invitadas
        solo pueden consultar los objetos que se les hayan compartido.
      </p>
    </section>
  );
}
