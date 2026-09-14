import { useEffect, useState, useRef } from "react";
import {
  Table2,
  Columns3,
  Code2,
  Play,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Search,
  KeyRound,
} from "lucide-react";
import { api, browseSql, cell, quote } from "./api";
import type { DataObject, QueryResult } from "./types";
export function ResultTable({ result }: { result: QueryResult }) {
  if (!result.columns)
    return (
      <div className="notice">
        Operación completada.{" "}
        {result.affected_rows !== undefined && result.affected_rows >= 0
          ? `${result.affected_rows} filas afectadas.`
          : ""}
      </div>
    );
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th className="row-number">#</th>
            {result.columns.map((name, i) => (
              <th key={i}>{name}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows?.map((row, i) => (
            <tr key={i}>
              <td className="row-number">{i + 1}</td>
              {row.map((v, j) => (
                <td
                  key={j}
                  title={cell(v)}
                  className={v === null ? "null-value" : ""}
                >
                  {cell(v)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {!result.rows?.length && (
        <div className="empty compact">No hay registros para mostrar.</div>
      )}
    </div>
  );
}
export function Explorer({
  databaseId,
  object,
  isOwner,
  onChanged,
}: {
  databaseId: string;
  object: DataObject | null;
  isOwner: boolean;
  onChanged: () => void;
}) {
  const request = useRef(0);
  const [tab, setTab] = useState<"data" | "schema" | "sql">("data");
  const [page, setPage] = useState(0);
  const [result, setResult] = useState<QueryResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("");
  const [revision, setRevision] = useState(0);
  const [sql, setSql] = useState(
    object ? `SELECT * FROM data.${quote(object.name)} LIMIT 50;` : "SELECT 1;",
  );
  useEffect(() => {
    const generation = ++request.current;
    setBusy(false);
    if (!object || tab !== "data")
      return () => {
        request.current++;
      };
    setBusy(true);
    setError("");
    setResult(null);
    api<QueryResult>("query", {
      database_id: databaseId,
      sql: browseSql(object, page),
    })
      .then((r) => {
        if (generation === request.current) setResult(r);
      })
      .catch((e) => {
        if (generation === request.current) setError(e.message);
      })
      .finally(() => {
        if (generation === request.current) setBusy(false);
      });
    return () => {
      request.current++;
    };
  }, [databaseId, object, page, revision, tab]);
  async function run() {
    const generation = ++request.current;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const r = await api<QueryResult>("query", {
        database_id: databaseId,
        sql,
      });
      if (generation === request.current) {
        setResult(r);
        onChanged();
      }
    } catch (e) {
      if (generation === request.current) setError((e as Error).message);
    } finally {
      if (generation === request.current) setBusy(false);
    }
  }
  const displayName = object?.display_name?.trim() || object?.name;
  const description = object?.description?.trim();
  const summary = object?.attributes_summary?.trim();
  const allRows = result?.rows || [];
  const rows = (tab === "data" ? allRows.slice(0, 50) : allRows).filter(
    (row) =>
      !filter ||
      row.some((value) =>
        cell(value).toLocaleLowerCase().includes(filter.toLocaleLowerCase()),
      ),
  );
  return (
    <section className="panel explorer">
      <div className="panel-head">
        <div>
          <div className="eyebrow">
            {object?.kind === "view" ? "VISTA" : "TABLA"} ·{" "}
            {object && displayName !== object.name
              ? `data.${object.name}`
              : "data"}
          </div>
          <h2>{displayName || "Explorador SQL"}</h2>
          {description && <p className="object-description">{description}</p>}
          {summary && summary !== description && (
            <p className="muted object-summary">{summary}</p>
          )}
          {!object && (
            <p className="muted">Consultá y organizá el contexto de tu base.</p>
          )}
        </div>
        <span className="badge">
          {isOwner ? "Propietario" : "Solo lectura"}
        </span>
      </div>
      <div className="tabs" role="tablist" aria-label="Explorador">
        {(
          [
            { id: "data", label: "Datos", icon: Table2 },
            { id: "schema", label: "Estructura", icon: Columns3 },
            { id: "sql", label: "Consulta SQL", icon: Code2 },
          ] as const
        ).map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => {
              setTab(id);
              setResult(null);
              setError("");
              setFilter("");
            }}
            disabled={!object && id !== "sql"}
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </div>
      {tab === "schema" && object ? (
        <div className="schema-content">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Columna</th>
                  <th>Tipo</th>
                  <th>Restricciones</th>
                  <th>Descripción</th>
                </tr>
              </thead>
              <tbody>
                {object.columns.map((col) => (
                  <tr key={col.name}>
                    <td>
                      <span className="inline">
                        {object.primary_key.includes(col.name) && (
                          <KeyRound size={13} />
                        )}{" "}
                        {col.name}
                      </span>
                    </td>
                    <td>
                      <code>{col.type}</code>
                    </td>
                    <td>
                      {object.primary_key.includes(col.name)
                        ? "Primary key"
                        : !col.nullable
                          ? "NOT NULL"
                          : "Admite NULL"}
                    </td>
                    <td>{col.description || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {object.foreign_keys?.map((fk, i) => (
            <p className="relation" key={i}>
              {fk.columns.join(", ")} → {fk.references.table}.
              {fk.references.columns.join(", ")}
            </p>
          ))}
        </div>
      ) : (
        <>
          {tab === "sql" ? (
            <div className="sql-box">
              <label htmlFor="sql-editor">
                {isOwner
                  ? "Consulta o cambio de esquema"
                  : "Consulta de lectura"}
              </label>
              <textarea
                id="sql-editor"
                spellCheck={false}
                value={sql}
                onChange={(e) => setSql(e.target.value)}
                rows={5}
              />
              <div className="sql-actions">
                <span className="muted">
                  {isOwner
                    ? "Los cambios se ejecutan en tu base."
                    : "Solo podés consultar objetos compartidos."}
                </span>
                <button
                  className="btn btn-primary"
                  onClick={run}
                  disabled={busy || !sql.trim()}
                >
                  <Play size={14} />
                  Ejecutar
                </button>
              </div>
            </div>
          ) : (
            <div className="data-toolbar">
              <label className="search-box">
                <Search size={15} />
                <input
                  aria-label="Filtrar esta página"
                  placeholder="Filtrar esta página…"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                />
              </label>
              <button
                className="btn btn-small"
                aria-label="Actualizar registros"
                disabled={busy}
                onClick={() => setRevision((x) => x + 1)}
              >
                <RefreshCw size={14} />
                Actualizar
              </button>
            </div>
          )}
          {error && (
            <div className="error" role="alert">
              {error}
            </div>
          )}
          {busy ? (
            <div className="empty" role="status">
              <span className="spinner" />
              Cargando registros…
            </div>
          ) : result ? (
            <ResultTable result={{ ...result, rows }} />
          ) : (
            !error && (
              <div className="empty">
                <Code2 size={30} />
                <h3>
                  {object ? "Ejecutá una consulta" : "Tu esquema empieza acá"}
                </h3>
                <p>
                  Tu asistente puede crear las tablas que necesites.
                  <br />
                  También podés usar la pestaña Consulta SQL.
                </p>
              </div>
            )
          )}
          {result && (
            <div className="table-footer">
              <span>
                {rows.length} filas {tab === "data" && `· Página ${page + 1}`}{" "}
                {result.truncated && "· Resultado limitado"}
              </span>
              {tab === "data" && (
                <div className="inline">
                  <button
                    className="icon-btn"
                    aria-label="Página anterior"
                    disabled={page === 0 || busy}
                    onClick={() => setPage((p) => p - 1)}
                  >
                    <ChevronLeft size={17} />
                  </button>
                  <button
                    className="icon-btn"
                    aria-label="Página siguiente"
                    disabled={allRows.length <= 50 || busy}
                    onClick={() => setPage((p) => p + 1)}
                  >
                    <ChevronRight size={17} />
                  </button>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </section>
  );
}
