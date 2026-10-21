# Backend requests

Things the frontend needs that the contract (docs/api.md, docs/openapi.json, docs/events.d.ts) does not give. Nothing is worked around.

## Resolved (the backend does these now, and the page uses them)
- `/liara/check` says why a token is not accepted (`code`, `params.status`); `team_name` in `/me` and `/settings`; `GET /session`; response schemas in the OpenAPI file; `networks[]` and `apps[]` are strings.
- Request expiry (`expires_at`), `max_heal_attempts` and `max_attempts`, `plan.links`, `monthly_price` on resources and on a project's live resources, the `step_finished.state` values, and "declining a fix ends as failed, no `stopped`".
- A Liara token that Liara rejects or cannot reach ends a run with `liara_token_invalid` or `liara_unreachable` (`reason_params.status`), not `internal_error`.
- `plan_ready.how_code` (+ `how_params.file`) and `would_send[].op` + `params`: the page words both from the catalog; the English text is only a fallback for an unknown code.

## Open
1. **`diagnosis.evidence[]` is prose with no code.** Please add a code and params, and the page will word it from the catalog:
   - `diagnosis.evidence[]` ("build took more than 5 minutes"): shown under a diagnosis.
   Everything else the page shows in Persian already comes from a code. `npm run report:fallbacks` prints this list from the samples.
