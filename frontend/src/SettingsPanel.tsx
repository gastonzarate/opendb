import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Save, RotateCcw, ShieldCheck, Plus } from "lucide-react";
import { api } from "./api";
import type { SavingInstructions } from "./types";

/** Reglas de guardado que el asistente lee con la herramienta
 * `saving_instructions`. Son preferencias del dueño: guían cuándo y cómo
 * guardar, pero nunca amplían permisos. */
const MAX_LENGTH = 4000;

const suggestions = [
  {
    label: "Cuándo guardar",
    text: "Guardá en OpenDB cada vez que te comparta una reunión, una factura o una nota que quiera recordar. Si no estás seguro de que deba guardarse, preguntame antes en una línea.",
  },
  {
    label: "Qué no guardar",
    text: "No guardes borradores, mensajes de prueba ni datos sensibles (contraseñas, tarjetas, documentos de identidad).",
  },
  {
    label: "Cómo modelar",
    text: "Usá una tabla por entidad real, con claves primarias y fechas en columnas propias. Conservá siempre el texto original junto con los datos estructurados.",
  },
  {
    label: "Nombres y unidades",
    text: "Nombrá tablas y columnas en minúsculas y en español, sin abreviaturas. Guardá importes con su moneda y fechas en zona horaria de Argentina.",
  },
  {
    label: "Cómo responderme",
    text: "Confirmá cada guardado en una o dos frases en español, sin mostrar SQL ni nombres de tablas salvo que lo pida.",
  },
] as const;

export function SettingsPanel({
  databaseId,
  isOwner,
}: {
  databaseId: string;
  isOwner: boolean;
}) {
  const id = useId();
  const lifetime = useRef(0);
  const [value, setValue] = useState("");
  const [saved, setSaved] = useState("");
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [confirmation, setConfirmation] = useState("");
  useEffect(() => {
    lifetime.current += 1;
    const started = lifetime.current;
    const current = () => lifetime.current === started;
    if (!isOwner) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError("");
    api<SavingInstructions>("saving_instructions", {
      database_id: databaseId,
    })
      .then((result) => {
        if (!current()) return;
        setValue(result.instructions);
        setSaved(result.instructions);
        setUpdatedAt(result.updated_at);
      })
      .catch((cause: unknown) => {
        if (current())
          setError(
            cause instanceof Error
              ? cause.message
              : "No pudimos leer tus reglas de guardado.",
          );
      })
      .finally(() => {
        if (current()) setLoading(false);
      });
    return () => {
      lifetime.current += 1;
    };
  }, [databaseId, isOwner]);
  const save = useCallback(async () => {
    if (saving) return;
    lifetime.current += 1;
    const started = lifetime.current;
    const current = () => lifetime.current === started;
    setSaving(true);
    setError("");
    setConfirmation("");
    try {
      const result = await api<SavingInstructions>(
        "update_saving_instructions",
        { database_id: databaseId, instructions: value },
      );
      if (!current()) return;
      setValue(result.instructions);
      setSaved(result.instructions);
      setUpdatedAt(result.updated_at);
      setConfirmation(
        "Guardamos tus reglas. Tu asistente las lee en su próxima consulta.",
      );
    } catch (cause) {
      if (current())
        setError(
          cause instanceof Error
            ? cause.message
            : "No pudimos guardar tus reglas.",
        );
    } finally {
      if (current()) setSaving(false);
    }
  }, [databaseId, saving, value]);
  const add = (text: string) =>
    setValue((old) => {
      if (old.includes(text)) return old;
      const trimmed = old.trimEnd();
      const next = trimmed ? `${trimmed}\n${text}` : text;
      return next.slice(0, MAX_LENGTH);
    });
  if (!isOwner)
    return (
      <section className="panel empty" aria-label="Configuración">
        <h3>Solo el dueño de la base configura las reglas</h3>
        <p>
          En una base compartida, las reglas de guardado las define quien la
          creó.
        </p>
      </section>
    );
  const dirty = value !== saved;
  return (
    <section className="panel stack settings-panel" aria-label="Configuración">
      <div className="panel-head">
        <div>
          <div className="eyebrow">REGLAS DE GUARDADO</div>
          <h2>Decile a tu asistente cómo guardar</h2>
          <p className="muted">
            Escribí en tus palabras cuándo guardar, qué dejar afuera y cómo
            organizar la información. Tu asistente lee estas reglas antes de
            guardar.
          </p>
        </div>
        <span className="badge">Se aplica a esta base</span>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="settings-suggestions">
        <span className="small muted">Sugerencias para empezar:</span>
        {suggestions.map((suggestion) => (
          <button
            key={suggestion.label}
            type="button"
            className="btn btn-small"
            onClick={() => add(suggestion.text)}
            disabled={loading || saving}
          >
            <Plus size={13} />
            {suggestion.label}
          </button>
        ))}
      </div>
      <label className="field" htmlFor={`${id}-instructions`}>
        Reglas de guardado
        <textarea
          id={`${id}-instructions`}
          className="settings-textarea"
          rows={12}
          maxLength={MAX_LENGTH}
          value={value}
          disabled={loading || saving}
          placeholder={
            loading
              ? "Cargando tus reglas…"
              : "Ejemplo: guardá cada reunión con sus participantes y decisiones; no guardes datos de tarjetas; confirmame cada guardado en una frase."
          }
          onChange={(event) => {
            setValue(event.target.value);
            setConfirmation("");
          }}
        />
      </label>
      <div className="settings-actions">
        <span className="small muted">
          {value.length}/{MAX_LENGTH} caracteres
          {updatedAt && ` · Última actualización: ${formatDate(updatedAt)}`}
        </span>
        <div className="inline">
          <button
            type="button"
            className="btn"
            onClick={() => {
              setValue(saved);
              setConfirmation("");
            }}
            disabled={!dirty || loading || saving}
          >
            <RotateCcw size={15} />
            Descartar cambios
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void save()}
            disabled={!dirty || loading || saving}
          >
            <Save size={15} />
            {saving ? "Guardando…" : "Guardar reglas"}
          </button>
        </div>
      </div>
      {confirmation && (
        <p className="small" role="status">
          {confirmation}
        </p>
      )}
      <p className="small muted inline settings-note">
        <ShieldCheck size={15} />
        Estas reglas orientan a tu asistente; no cambian permisos ni comparten
        datos. Los accesos se siguen definiendo en Accesos.
      </p>
    </section>
  );
}

function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("es-AR", { dateStyle: "medium", timeStyle: "short" });
}
