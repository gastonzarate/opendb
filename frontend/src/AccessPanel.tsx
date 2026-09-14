import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { api } from "./api";
import type { DataObject } from "./types";
import "./AccessPanel.css";

type PanelProps = {
  databaseId: string;
  objects: DataObject[];
  isOwner: boolean;
};
type AccessRole = {
  id: string;
  name: string;
  description?: string;
  objects: string[];
  emails: string[];
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
    <OwnerAccess key={databaseId} databaseId={databaseId} objects={objects} />
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
  const [selectedId, setSelectedId] = useState("");
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
      if (
        typeof payload.description === "string" &&
        payload.description.length > 2000
      ) {
        throw new Error("La descripción admite como máximo 2000 caracteres.");
      }
      const changed = await api<{ id?: string } | null>(action, payload);
      if (!current()) return;
      // Mutations may return null or partial records. Always read authoritative roles.
      const result = await api<AccessRole[]>("list_access", {
        database_id: databaseId,
      });
      if (current()) {
        setRoles(result);
        if (action === "create_role") {
          const created =
            result.find((role) => role.id === changed?.id) ??
            result.find((role) => role.name === payload.name);
          if (created) setSelectedId(created.id);
        }
        if (action !== "update_role") form?.reset();
      }
    });
  };
  const create = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const name = String(new FormData(form).get("name") ?? "").trim();
    const description = String(
      new FormData(form).get("description") ?? "",
    ).trim();
    if (name)
      mutate(
        "create_role",
        {
          database_id: databaseId,
          name,
          ...(description ? { description } : {}),
        },
        form,
      );
  };

  const selectedRole =
    roles?.find((role) => role.id === selectedId) ?? roles?.[0];
  return (
    <section className="panel stack access-panel" aria-label="Accesos">
      <div className="panel-head">
        <div>
          <h2>Accesos</h2>
          <p className="muted">
            Crea un rol, selecciona qué puede consultar e invita a las personas
            que lo usarán.
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
      <p className="muted">
        La descripción orienta al asistente y no concede permisos
        automáticamente. Cada tabla o vista se comparte explícitamente y los
        invitados tienen acceso de solo lectura.
      </p>
      <form className="form-row" onSubmit={create} aria-label="Crear rol">
        <label className="field">
          Nombre del rol
          <input name="name" required maxLength={100} disabled={pending} />
        </label>
        <label className="field">
          Descripción del rol
          <textarea
            maxLength={2000}
            name="description"
            rows={2}
            disabled={pending}
            placeholder="Explica para qué sirve este rol y qué datos debe consultar el asistente."
          />
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
      <div className="access-layout">
        <nav className="stack access-roles" aria-label="Roles de acceso">
          {roles?.map((role) => (
            <button
              type="button"
              className="access-role-card"
              key={role.id}
              aria-label={`Seleccionar rol ${role.name}`}
              aria-pressed={selectedRole?.id === role.id}
              disabled={pending}
              onClick={() => setSelectedId(role.id)}
            >
              <strong>{role.name}</strong>
              <span>{role.description || "Sin descripción"}</span>
              <small>
                {role.objects.length} tablas/vistas · {role.emails.length}{" "}
                personas
              </small>
            </button>
          ))}
        </nav>
        {(selectedRole ? [selectedRole] : []).map((role) => {
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
              <form
                className="stack"
                key={JSON.stringify([
                  role.id,
                  role.name,
                  role.description ?? "",
                ])}
                onSubmit={(event) => {
                  event.preventDefault();
                  const data = new FormData(event.currentTarget);
                  const name = String(data.get("name") ?? "").trim();
                  if (!name) return;
                  const description = String(
                    data.get("description") ?? "",
                  ).trim();
                  mutate("update_role", {
                    database_id: databaseId,
                    role_id: role.id,
                    name,
                    description,
                  });
                }}
              >
                <label className="field">
                  Nombre del rol seleccionado
                  <input
                    name="name"
                    required
                    maxLength={100}
                    defaultValue={role.name}
                    disabled={pending}
                  />
                </label>
                <label className="field">
                  Descripción para {role.name}
                  <textarea
                    maxLength={2000}
                    name="description"
                    rows={3}
                    defaultValue={role.description ?? ""}
                    disabled={pending}
                  />
                </label>
                <button className="btn" disabled={pending}>
                  Guardar cambios del rol
                </button>
              </form>
              <div>
                <h4>Tablas y vistas con permiso de lectura</h4>
                {role.objects.length === 0 && (
                  <p className="muted">
                    Sin tablas ni vistas compartidas. Este rol todavía no
                    permite consultar datos.
                  </p>
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
                <h4>Personas invitadas · Solo lectura</h4>
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
