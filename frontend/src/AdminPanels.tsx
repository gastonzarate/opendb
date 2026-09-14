import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { DataObject } from "./types";

type PanelProps = {
  databaseId: string;
  objects: DataObject[];
  isOwner: boolean;
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

export { AccessPanel } from "./AccessPanel";

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
            Los textos largos y las transcripciones se indexan automáticamente.
            Acá podés revisar su estado y buscar por significado.
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
          <h3>Agregar otro campo (opcional)</h3>
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

export { ConnectPanel } from "./ConnectPanel";
