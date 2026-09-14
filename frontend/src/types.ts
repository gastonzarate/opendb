export interface Database {
  id: string;
  status: string;
  is_owner: boolean;
  onboarding_completed?: boolean;
}
export interface Column {
  name: string;
  type: string;
  nullable: boolean;
  identity?: string;
  has_default?: boolean;
  description?: string;
}
export interface DataObject {
  name: string;
  display_name?: string;
  attributes_summary?: string;
  kind: string;
  description: string;
  columns: Column[];
  primary_key: string[];
  foreign_keys?: {
    columns: string[];
    references: { table: string; columns: string[] };
  }[];
}
export interface Catalog {
  fingerprint: string;
  objects: DataObject[];
}
export interface QueryResult {
  columns?: string[];
  rows?: unknown[][];
  truncated?: boolean;
  affected_rows?: number;
}
export interface Session {
  user: { id: number; email: string };
  csrf_token: string;
}
export interface Bootstrap {
  csrf_token: string;
  google_configured: boolean;
  mcp_url: string;
}
