export const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Organization = { id: string; name: string; role: string };
export type User = { id: string; email: string; organizations: Organization[]; platform_admin: boolean };
export type Resource = { id: string; category: string; name: string; unit: string };
export type Location = { id: string; name: string; kind: string; latitude: number; longitude: number; organization_id: string; distance_m?: number };
export type Lot = { id: string; resource_name: string; resource_id: string; unit: string; location_name: string; location_id: string; lot_code: string; expires_on: string | null; on_hand: string; reserved: string; available: string; created_at: string };
export type Item = { id: string; resource_name: string; resource_id: string; unit: string; quantity: string; allocated: string };
export type RequestRow = { id: string; priority: string; status: string; reason: string; required_by: string; created_at: string; delivery_location: string; latitude: number; longitude: number };
export type Candidate = { request_item_id: string; lot_id: string; resource_name: string; source_name: string; organization_id: string; available: string; requested: string; expires_on: string | null; distance_m: number; score: number; components: Record<string, number> };
export type Allocation = { id: string; request_id: string; requester_id: string; supplier_id: string; resource_name: string; quantity: string; shipment_status: string; shipment_id: string | null };
export type Shipment = { id: string; status: string; source: string; destination: string; source_lat: number; source_lon: number; target_lat: number; target_lon: number };
export type Audit = { id: number; action: string; entity_type: string; entity_id: string; occurred_at: string; before_state: unknown; after_state: unknown; actor_id: string; correlation_id: string };
export type Dashboard = { metrics: { open_requests: number; critical_requests: number; available_units: string; expiring_lots: number; allocations: number; fulfilled_requests: number; avg_delivery_hours: number | null }; resource_flow: { name: string; allocated: string }[]; activity: Audit[] };

export class ApiError extends Error {
  constructor(public code: string, message: string, public status: number) { super(message); }
}

export function csrfToken(): string {
  return document.cookie.split("; ").find((part) => part.startsWith("csrf="))?.split("=")[1] || "";
}

export async function api<T>(path: string, org?: string, options: RequestInit = {}): Promise<T> {
  const method = options.method || "GET";
  const headers = new Headers(options.headers);
  if (org) headers.set("X-Organization-ID", org);
  if (method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken());
    if (options.body) headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API}${path}`, { ...options, headers, credentials: "include", cache: "no-store" });
  const body = await response.json();
  if (!response.ok) throw new ApiError(body.error?.code || "REQUEST_FAILED", body.error?.message || "Request failed", response.status);
  return body as T;
}

export function mutation<T>(path: string, org: string, body?: unknown, method = "POST", idempotent = false): Promise<T> {
  return api<T>(path, org, { method, body: body === undefined ? undefined : JSON.stringify(body),
    headers: idempotent ? { "Idempotency-Key": crypto.randomUUID() } : {} });
}
export const formatDate = (value: string | null) => value ? new Date(value).toLocaleString("en-US", { dateStyle: "medium", timeStyle: value.includes("T") ? "short" : undefined }) : "No expiry";
