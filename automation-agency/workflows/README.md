# n8n Workflow Exports

Committed nightly. **The n8n UI is not a source of truth** — this directory is.

```bash
# Export everything (run from the host, nightly via cron)
docker compose exec -T n8n n8n export:workflow --all --output=/workflows/
git add workflows/ && git commit -m "chore: nightly workflow export"
```

## Naming

`{niche}-{NN}-{slug}.json` — e.g. `re-02-lead-instant-response.json`,
`ms-05-waitlist-backfill.json`. The numbers match the workflow catalogue in
§3.6 of the main [README](../README.md).

## Change control

Never edit a live workflow in the UI. Duplicate → suffix `-dev` → change →
test against the seeded test tenant → export → commit → read the diff →
activate `-dev`, deactivate the old → keep the old for seven days.

## Invariants

Every production workflow must:

1. Set `app.tenant_id` as its first action (use the shared sub-workflow).
2. Call `POST /consent/check` before any **marketing** send, and short-circuit
   on `false`. Utility and service messages are exempt; nothing else is.
3. Have an error workflow attached that writes to `core.event` and alerts ops.
4. Carry an idempotency key on anything that sends. Meta retries webhooks.
