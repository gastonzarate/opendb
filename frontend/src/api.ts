import type { DataObject } from "./types";
let csrf = "";
export const setCsrf = (value: string) => {
  csrf = value;
};
export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function read<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    credentials: "same-origin",
    cache: "no-store",
  });
  const data = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      data?.error?.message || "No se pudo cargar la información.",
      response.status,
    );
  return data as T;
}
export async function api<T>(
  action: string,
  payload: Record<string, unknown> = {},
): Promise<T> {
  const response = await fetch(`/api/actions/${encodeURIComponent(action)}/`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      data?.error?.message ||
        (response.status === 401
          ? "Tu sesión terminó. Volvé a iniciar sesión."
          : "No se pudo completar la operación. Actualizá la página e intentá nuevamente."),
      response.status,
    );
  return data.result as T;
}
export const quote = (name: string) => '"' + name.replaceAll('"', '""') + '"';
export function browseSql(object: DataObject, page: number) {
  if (!Number.isSafeInteger(page) || page < 0)
    throw new Error("Página inválida");
  const order = object.primary_key.length
    ? ` ORDER BY ${object.primary_key.map(quote).join(", ")}`
    : "";
  return `SELECT * FROM data.${quote(object.name)}${order} LIMIT 51 OFFSET ${page * 50}`;
}
export function cell(value: unknown): string {
  return value === null
    ? "NULL"
    : typeof value === "object"
      ? JSON.stringify(value)
      : String(value);
}
