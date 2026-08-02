import { Pool } from "pg";

/**
 * Read-only access to the analytics read models.
 *
 * Two things make this safe, and both must stay true:
 *
 *   1. The connection uses agency_portal_ro, which has SELECT and nothing
 *      else. The portal is physically incapable of writing.
 *   2. Every query runs inside a transaction that sets app.tenant_id, so
 *      row-level security scopes the result to the signed-in client. The
 *      analytics views are security_invoker, which is what makes RLS evaluate
 *      as the portal role rather than the view owner.
 *
 * Never build a query by concatenating a tenant id into SQL, and never expose
 * a route that takes tenant_id from the request. It comes from the session,
 * always.
 */
const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  max: 10,
  idleTimeoutMillis: 30_000,
});

export async function queryForTenant<T = Record<string, unknown>>(
  tenantId: string,
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    // Transaction-local: a pooled connection cannot carry this into the next
    // request even if something throws before COMMIT.
    await client.query("SELECT set_config('app.tenant_id', $1, true)", [tenantId]);
    const result = await client.query(sql, params);
    await client.query("COMMIT");
    return result.rows as T[];
  } catch (err) {
    await client.query("ROLLBACK");
    throw err;
  } finally {
    client.release();
  }
}

/**
 * Format a RAND amount: `R164 500` — non-breaking space separator, no gap
 * after the R. Getting the separator wrong is a small tell that reads as
 * foreign.
 *
 * Takes RANDS, not cents. The analytics views are the single place cents are
 * converted (`round(sum(x) / 100.0, 2)`), so nothing above them should divide
 * or multiply by 100 again. Doing it twice turns R169 100 into R16 910 000 —
 * the kind of error a client spots before you do.
 */
export function rands(amount: number | string | null | undefined): string {
  const value = Number(amount ?? 0);
  return "R" + Math.round(value).toLocaleString("en-ZA").replace(/,/g, " ");
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
}
