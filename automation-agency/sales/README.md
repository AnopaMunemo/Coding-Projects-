# Sales Assets

Scripts, proposal templates and case studies. The scripts themselves live in
§6 of the main [README](../README.md); this directory holds the artefacts you
send.

```
sales/
├── proposal-template.md      Three pages. Not fifteen.
├── discovery-notes.md        The five questions, per niche
├── case-studies/             One per niche per quarter, real numbers, permissioned
└── audit-microsite/          Manus template for per-prospect audit pages
```

## Rules

- **Every proposal uses the prospect's own numbers**, taken from the audit run
  in `tools/prospect_audit.py`. A proposal with generic figures is a brochure.
- **Underselling is the highest-trust move available.** Show the conservative
  case, say out loud that it is conservative, and commit to showing the real
  number weekly.
- **Log every approach** in `agency.approach_log` before you send anything.
  POPIA s69 permits a single approach to request consent — see §6.2.
- **Case studies need written permission** naming the client, the numbers, and
  the logo. Get it in the contract at signature, not afterwards.
