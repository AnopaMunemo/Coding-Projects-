/**
 * Tenant resolution for the portal.
 *
 * SECURITY INVARIANT: the tenant id comes from the authenticated session and
 * from nowhere else. Never a query parameter, never a header, never a path
 * segment — any of those let a signed-in client read another client's pipeline
 * by editing a URL.
 *
 * Left deliberately unimplemented so it cannot be forgotten. Wire it to your
 * auth provider (Auth.js, Clerk, WorkOS) and return the tenant id stored on
 * the session at login.
 */
export async function getTenantId(): Promise<string> {
  throw new Error(
    "getTenantId(): connect this to your auth provider before deploying. " +
      "See portal/lib/session.ts.",
  );
}
