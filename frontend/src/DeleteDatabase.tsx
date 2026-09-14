import { useRef, useState } from "react";
import { Trash2 } from "lucide-react";
import { api } from "./api";
import type { Database } from "./types";

export function DeleteDatabase({
  database,
  onDeleted,
  onUnavailable,
}: {
  database: Database;
  onDeleted: (db: Database) => void;
  onUnavailable?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function remove() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const deleted = await api<Database>("delete_database", {
        database_id: database.id,
      });
      dialog.current?.close();
      onDeleted(deleted);
    } catch {
      onUnavailable?.();
      setError(
        "No pudimos completar el borrado. Reintentá para terminar de eliminar la base.",
      );
    } finally {
      setBusy(false);
    }
  }
  if (!database.is_owner || database.status === "deleted") return null;
  return (
    <div className="delete-database">
      <button
        className="btn btn-danger"
        onClick={() => {
          setError("");
          dialog.current?.showModal();
        }}
      >
        <Trash2 size={15} />
        Borrar base de datos
      </button>
      <dialog
        ref={dialog}
        className="delete-dialog"
        aria-labelledby="delete-title"
        aria-describedby="delete-description"
        onCancel={(event) => {
          if (busy) event.preventDefault();
        }}
      >
        <h2 id="delete-title">¿Borrar tu base de datos?</h2>
        <p id="delete-description">
          Se eliminarán todos tus datos, tablas, vistas, índices y accesos
          compartidos. Esta acción no se puede deshacer. Tu cuenta seguirá
          activa.
        </p>
        {error && <p role="alert">{error}</p>}
        <div className="delete-dialog-actions">
          <button
            className="btn"
            autoFocus
            disabled={busy}
            onClick={() => dialog.current?.close()}
          >
            Cancelar
          </button>
          <button
            className="btn btn-danger"
            disabled={busy}
            onClick={() => void remove()}
          >
            {busy ? "Borrando…" : "Borrar definitivamente"}
          </button>
        </div>
      </dialog>
    </div>
  );
}
