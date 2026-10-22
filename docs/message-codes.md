# Message codes (the translation contract)

Everything the backend says about its own decisions is a **stable code plus parameters**. The UI owns the wording in each language,
so a native speaker can review one catalog. Every code also has an English `message` / `text` fallback in the event, which the UI may show if it has no translation for a code yet.
Text that the AI model writes itself (agent notes, agent questions, `agent_message`) cannot be coded: it has `code: null` and arrives in the language set by `Settings.language` (`en` or `fa`).
Identifiers (file names, paths, variable names, ports, versions, service names) are parameters or fallback text and must stay left-to-right, untranslated.

Codes appear in: plan `notes[]`, plan `questions[]` and `question` events, `confirm_request`, `warning`, `diagnosis` (+ its `fixes[]`), `fix_applied`/`fix_declined`, `run_finished.reason_code`.

## Plan notes (`plan.notes[].code`)
| code | level | params |
|---|---|---|
| `service_privileged` | blocker | `service` |
| `service_host_network` | blocker | `service` |
| `compose_profile` | info | `service` |
| `database_wired` | info | `app`, `database`, `env_var`, `detected` (the app reaches the database through this variable; `detected` = the code reads that name) |
| `platform_version` | info | `app`, `platform`, `version`, `source`, `wanted` (the app builds with a runtime version taken from the project's own file) |
| `credentials_wired` | info | `service`, `key` |
| `credentials_unmapped` | warning | `service`, `key` |
| `host_path_not_mountable` | warning | `service`, `source`, `target` |
| `db_version_substituted` | warning | `kind`, `wanted`, `used` |
| `db_init_scripts` | warning | `service` |
| `db_experimental` | warning | `kind` |
| `proxy_dropped` | info | `service`, `image` |
| `question_answered` | info | `question_code` (may be null), `question` (the question text, for questions without a code), `answer` (an option id when the question had options) |

## Questions (`question` event and `plan.questions[]`)
| code | params | options (ids to localize) |
|---|---|---|
| `worker_without_port` | `service` | `worker`, `needs_port` |
| `multiple_ports` | `service`, `ports`, `chosen` | none (free text) |
| `code_fix_choice` | `diagnosis` (the cause code), `summary`, `evidence[]`, `location` (file:line or ""), `sakoo_can_fix` (false: no model and no rule can fix it, so the card should say so and not offer it), `mcp_url`, `app` | `sakoo`, `own_agent`, `sakoo_remember`, `own_agent_remember` (the `_remember` variants also keep the answer for the project) |
Questions written by the model have `code: null`; their `options` (if any) are plain answer texts in the user's language.

## Approvals (`confirm_request.code`)
| code | kind | params |
|---|---|---|
| `create_resources` | plan | `apps`, `databases`, `hourly`, `monthly` (toman, incl. VAT) |
| `resize_tier` | fix | `plan`, `tier`, `tier_label`, `from_tier`, `from_tier_label`, `app`, `build_minutes`, `from_build_minutes` |
| `resize_plan` | fix | `plan`, `tier`, `from_plan`, `app`, `ram_gb`, `from_ram_gb` |
| `set_port` | fix | `port`, `from_port` |
| `patch_add_dependency` | patch | `file`, `append` |
| `patch_node_engine` | patch | `file`, `engines_node`, `platform_version`, `expected` |
| `patch_add_npm_dependency` | patch | `file`, `add_dependency` (the package; its current version is looked up on the npm registry when the patch is applied) |
| `repair_code` | patch | `files[]`, `env[]` (names only), `summary` (what the model says it changes, in the user's language), `diff` (the unified diff, up to 8000 characters: show it on the card, the person decides on it), `digest`, `steps` (a model read the project and proposes these changes to Sakoo's copy; the full diff arrives as `patch_ready`) |

## Warnings (`warning.code`)
`repair_gave_up` {`reason`} (the model could not find a safe code fix: said in words, then the failure is reported as it is), `slow_start` {`minutes`} (the app answers 5xx but its logs show no crash: the agent waits up to 6 more minutes), `app_renamed` {`old`, `new`} (Liara answered 409: the name is used by another user; the app got a short random ending), `unresolved_db_vars` {`names`}, `secret_file_not_uploaded` {`file`}, `missing_env_values` {`names`}.

## Diagnoses (`diagnosis.code`, `diagnosis.params`)
| code | params |
|---|---|
| `build_timeout` | `tier_label`, `limit_minutes` |
| `out_of_memory` | `ram_gb` |
| `missing_python_module` | `module` |
| `missing_node_module` | `module` |
| `node_engine_mismatch` | `expected`, `version`, `engines_node` |
| `missing_env` | `names` |
| `db_auth_failed` | none |
| `db_localhost` | `kind` |
| `outbound_blocked` | `hosts` |
| `wrong_port` | `actual`, `expected` |
| `bound_to_localhost` | none |
| `no_dockerfile` | none |
| `not_serving` | `http` |
| `code_error` | `location` (file:line in the project, or "") |
| `unknown` | `state`, `reason` |

`diagnosis.category` says who has to change what: `code` (the developer's source: `missing_python_module`, `missing_node_module`, `node_engine_mismatch`, `db_localhost`, `bound_to_localhost`, `code_error`; the run asks `code_fix_choice`), `config` (settings on Liara: `missing_env`, `db_auth_failed`, `wrong_port`, `not_serving`), `resources` (a bigger plan, costs money: `build_timeout`, `out_of_memory`), `outside` (nothing a redeploy fixes: `outbound_blocked`, `no_dockerfile`), `unknown`.

## Fixes (`diagnosis.fixes[].code`, also `fix_applied.code`)
Each fix event is `{code, kind, text, params, cost_delta_hourly}`.
`resize_tier`, `resize_plan` (cost > 0), `set_port`, `set_env`, `patch_add_dependency`, `patch_add_npm_dependency`, `patch_node_engine`, `repair_code` (changes to Sakoo's copy, asked first unless the person turned auto-fix on), `refresh_db_credentials` (free, applied without asking),
and manual advice (no action by us): `shorten_build`, `reduce_memory`, `declare_dependency`, `npm_install`, `outbound_options`, `db_point_host`, `db_create`,
`bind_all_interfaces`, `add_dockerfile`, `check_port_and_bind`, `read_logs`, `fix_code_error`; and the two entries a hand-off to the developer's own agent leaves in the run's fixes: `handoff_fix` (their agent sent files), `handoff_redeploy` (their agent pushed to Git and Sakoo released the newest code).

## Run outcome (`run_finished.reason_code`)
`no_plan`, `agent_unavailable`, `blocked`, `not_approved`, `deploy_failed`, or any diagnosis code when the failure was diagnosed.
Liara refused a request: `liara_token_invalid` (401), `insufficient_balance` (402, top up the account), `liara_forbidden` (403), `release_failed` (a release of new code on a running app did not go live: `reason_params.state`, `reason_params.detail`; the app keeps its previous release; the run has `mode: release`, nothing to delete), `deploy_limit_reached` (428 from Liara, or 409 from this server before it asks: the app used its releases for the day; `params` {`used`, `cap`, `tier`}), `name_taken` (409; app names are shared by all users, so the agent first retries with a random ending and this is only the last resort), `liara_rate_limited` (429), or the generic `liara_rejected`. `reason_params.status` has the HTTP status and `reason_params.detail` Liara's own short reason (for example `g2_plan_unavailable`).
Other: `database_not_ready` (`reason_params.id`: the database never reported working credentials).
`run_finished.created` lists names, not Liara's internal ids: `{apps: [name], databases: [name], database_ids: [liara id]}`.

## Readiness findings (`plan_ready.readiness.findings[]`)
Each finding is `{code, level, file, line, params}`; `readiness.counts` is `{warning: n, info: n}`. Findings never contain a secret value, only where it is.
| code | level | params |
|---|---|---|
| `env_file_present` | warning | `count` (variables with values in the file; it is not uploaded) |
| `hardcoded_secret` | warning | `kind` (aws_access_key, private_key, api_key, assigned_secret) |
| `debug_enabled` | warning | none |
| `allowed_hosts_wildcard` | info | none |
| `sqlite_data_loss` | warning | none (data on the app's disk is lost on redeploy unless a Liara disk or database is used) |
| `binds_localhost` | warning | none |
| `writes_local_files` | info | none (suggest a disk) |
| `foreign_service_dependency` | warning | `service` (telegram, openai, anthropic, google_ai, discord: may be unreachable from Liara's network) |

## Fix parameters (what each fix's wording may use)
| fix code | params |
|---|---|
| `resize_tier` | `plan`, `tier`, `tier_label`, `from_tier`, `from_tier_label`, `app`, `build_minutes`, `from_build_minutes` |
| `resize_plan` | `plan`, `tier`, `from_plan`, `app`, `ram_gb`, `from_ram_gb` |
| `set_port` | `port`, `from_port` |
| `set_env` | `names` (variable names to ask the user for) |
| `patch_add_dependency` | `file`, `append` (the dependency line added to the copy) |
| `patch_node_engine` | `file`, `engines_node` (the relaxed range written to the copy), `platform_version` (the Node version the build uses), `expected` (the original pin) |
| `patch_add_npm_dependency` | `file`, `add_dependency` |
| `repair_code` | `files[]`, `env[]`, `summary`, `digest`, `steps` |
| `fix_code_error` | `location` (may be absent) |
| `declare_dependency` | `module` |
| `npm_install` | `module` |
| `outbound_options` | `hosts` |
| `db_point_host`, `db_create` | `kind` (postgres, mysql, ...) |
| `refresh_db_credentials`, `shorten_build`, `reduce_memory`, `bind_all_interfaces`, `add_dockerfile`, `check_port_and_bind`, `read_logs` | none (the diagnosis `params` carry the numbers) |

## Plan source and planned requests
`plan_ready.how_code`: `from_compose` {`file`} (the plan came from that file) or `investigated_by_agent`. `plan_ready.would_send[].op` + `params`: `create_app` {name, platform, plan, tier}, `create_database` {name, type, version, plan, tier}, `set_env` {app, count}, `upload_source` {app}, `create_release` {app, port}, `resize_app` {plan, tier}, `update_port` {app, port}, `create_disk` {app, name, size (GB)}, `create_ftp` {app, disk} (a temporary FTPS access to deliver wp-content, removed right after). The English `summary` stays as a fallback.
Evidence `note`s on chips (for example "service web", "dependency pg") are short identifiers, not sentences: show them as written.

## Run outcomes added by the web layer (`run_finished.reason_code`)
`team_required` (the Liara account belongs to a team; choose it in settings), `network_required` (several private networks; `reason_params.networks` lists them), `internal_error`.

## Plan notes added by the preflight
`name_taken` (blocker) {`kind`: app or database, `name`}: that name already exists on the user's Liara account. Nothing is created; the user renames the project.

## API errors (HTTP)
Every error response is `{"detail": {"code", "message", "params"?}}`; `message` is an English fallback. The UI should localize by `code`. Params: `upload_too_large` {limit_mb}, `too_large` {limit_mb}, `too_many_files` {limit}, `git_host` {host, allowed[]}; the numbers are also available up front from `GET /api/limits`.

| status | codes |
|---|---|
| 401 | `not_logged_in`, `bad_credentials` |
| 403 | `csrf` |
| 404 | `no_patch` (the agent changed nothing in this project), `not_found`, `no_such_request` (an approval/question that is no longer waiting, e.g. it expired) |
| 409 | `not_waiting` (the run is not waiting for the developer's own agent), `too_many_tokens` (10 active agent tokens), `username_taken`, `run_active`, `no_live_app` (deploy first: a domain needs a running app this agent created), `domain_taken` (the domain is already attached to an app on Liara) |
| 403 | `not_owned` (the agent did not create that app, domain or DNS record, so it will not change it) |
| 404 (domains) | `domain_not_found` |
| 404 (history) | `release_not_found` |
| 409 (history) | `release_current` (already the running one), `release_not_ready` (only a READY release can be rolled back to), `rollback_unavailable` |
| 422 (domains) | `domain_invalid`, `domain_is_liara_address`, `domain_unsupported_tld` {tld}, `confirm_required` (a domain is only created after the plan was approved) |
| 422 (account) | `bad_username`, `weak_password`, `bad_language`, `bad_mode`, `confirm_mismatch`, `bad_choice` (the remembered code-fix choice must be `sakoo`, `own_agent` or null), `bad_option` (an answer that is not one of the question's `options`) |
| 422 (project source) | `source_required`, `upload_too_large`, `bad_zip`, `too_many_files`, `too_large`, `path_traversal`, `symlink`, `empty`, `git_scheme`, `git_credentials`, `git_host`, `git_branch`, `git_token_invalid`, `git_path`, `git_failed`, `git_timeout`, `git_missing` |
| 429 | `rate_limited`, `too_many_runs` |
| 400 | `no_liara_token` |
| 422 (alerts) | `webhook_scheme` (must be https), `webhook_credentials`, `webhook_unresolved`, `webhook_private` (the address points inside a private network) |
| 409/4xx (pull request) | `pr_no_patch` (the agent changed nothing), `pr_needs_token` (a token that can write is needed), `pr_not_github`, `pr_bad_path`, `github_token_rejected` (400), `github_no_write` (403: the token cannot write), `github_not_found` (404), `github_rejected` (502) {status}, `github_unreachable` (502) |
| release `deploys[].error` | `no_previous_release` (no earlier deploy to copy the settings from), a `git_*` code (the clone failed), or `the release ended <STATE>` (Liara built it and it did not become healthy) |
| GitHub sign-in | `github_oauth_off` (409, no OAuth app on this server), `github_oauth_state` (400, the sign-in was not started from here), `github_oauth_failed` (400, no token for that code), `github_not_connected` (409) |
| WordPress upload | `wp_no_content` (409), `wp_too_large`, `wp_nothing_to_send`, `wp_bad_host`, `wp_host_unresolved`, `wp_host_private` (422); job `error`: `wp_tls_failed` (certificate not verified), `wp_login_failed`, `wp_ftp_refused`, `wp_ftp_unreachable`; `wp_busy` (409) |
| 403 (register) | `invite_required` (the server has `WEB_REGISTRATION_CODE` and the request's `invite` did not match; `GET /limits` says `registration_code_required`) |
| 409 (deploy) | `deploy_busy` (a deployment of this project is already running) |
| 409 (alerts) | `no_webhook` (set an alert address first) |
| 409 (branches) | `not_a_git_project` (only a project from a Git repository can preview a branch) |
| 409/422 (chat) | `chat_busy` (the assistant is still answering), `action_pending` (a proposed change waits for the person's decision before another message), `chat_full` (the conversation reached its length limit: clear it), `action_invalid` (422: the card was already decided, the project name typed does not match, or a value is missing) |
| chat cards (`code`, `params`) | `set_env` {name, project}, `unset_env`, `resize_plan` {project, from, to}, `rollback` {project, release, message}, `redeploy` {project, branch}, `add_domain` {project, domain}, `start_run_` + `preview` or `deploy` {project}, `cleanup` {project, resources}, `install_ready_made` {app, name}, `create_from_git` {url, branch}, `edit_files` {project, n} (+ `files`, `diff`), `undo_edit`, `deploy_edits`, `new_project` {name}; warnings (`warnings[].code`): `restart`, `restart_brief`, `rebuild`, `nothing_new`, `build_minutes`, `dns_records`, `approve_each`, `irreversible`, `nothing_created`, `edit_local`, `edit_not_in_repo`. The page words them as `chat.card.*` and `chat.warn.*` |
| network step | question `network_choice` {new_name} (options: `create_new` or the names of the account's networks), notes `network_new` / `network_existing` {name}; `plan_ready.network` is `{name, new}` or null (no network needed) |
| fixed-rules plan | `plan_ready.how_code` `from_rules`; note `planned_without_model` (warning). Used when the AI model cannot be used and the project is one ordinary app (see `plan/fallback.py`); otherwise the run ends `agent_unavailable` {why} |
| stopping a run | outcome `run_cancelled` (the person pressed Stop: `stopped: cancelled`, what was already created stays, the page offers to delete it); `POST /runs/{id}/cancel` answers 409 `run_not_active` when the run is not going |
| 409 (explain) | `nothing_to_explain` (the run's log has nothing in it yet) |
| 502 | `liara_unreachable`, `cleanup_failed`, `llm_failed` (the language model could not be reached), `webhook_failed` (the alert address did not accept the message), `llm_empty` |
| 503 | `llm_unavailable` (no language model is configured on the server) |

## Custom domains (`/api/projects/{id}/domains`)
`GET` lists the app's domains with status and the DNS records Liara asks for; `POST .../plan` reads what connecting would do (nothing is created); `POST` creates (body `confirm: true` after the person approved the plan); `POST .../{domain}/check` re-checks the records (and the nameserver check of a pending Liara DNS zone); `GET .../{domain}/dns-check` asks the zone's own nameservers and Google, Cloudflare and Quad9 about each record (`verdict`: `all_good`, `propagating` = right at the source and caches still catching up, `missing`, `wrong_value`, `not_at_liara`, `no_nameservers`, `unknown`); `POST .../{domain}/ssl` requests the free certificate; `GET .../{domain}/removal` lists the DNS records Liara added for the domain to a Liara DNS zone (route ALIAS/CNAME to a Liara host, the SSL challenge CNAME, the ownership TXT; a record with such a name that points elsewhere is the developer's own and is never listed); `DELETE .../{domain}` detaches a domain this agent attached and, with `?remove_records=true`, deletes those listed records too (default: DNS is not touched).

Plan steps (`steps[].code`, `params`):

| code | params | meaning |
|---|---|---|
| `zone_create` | `zone` | a DNS zone for the domain is created on Liara's DNS service |
| `zone_nameservers` | `zone` | the person sets Liara's nameservers at their registrar (the zone stays pending until then) |
| `domain_attach` | `domain`, `app` | the domain is attached to the app |
| `records_added` | `domain` | with Liara DNS the records Liara asks for are added to the zone |
| `records_manual` | `domain` | with other DNS the person sets the records shown and uses "check records" |
| `www_redirect` | `domain`, `www` | www.<domain> is attached too and redirected (301) to the bare domain |
| `ssl_enable` | `domain` | the free SSL certificate is requested once the records are in place |

`DomainOut.records[]` is `{type, name, value, purpose}`: Liara's public spec does not say where the records are in its answer, so they are found by shape; `detail_keys` lists the fields Liara returned, and when `records` is empty the UI should point to the console's domain page (`DomainsOut.console_url`). `zone_check` is `checked`, `busy` (Liara is already checking: try again later) or `not_pending`.

## Storage, email and WordPress (asked in the plan phase, before anything is created)
Questions (`question.code`; options are stable ids, worded from `msg.option.<code>.<id>`):

| code | options | params |
|---|---|---|
| `storage_choice` | `create_bucket`, `keep_current` | `monthly` (toman incl. VAT) |
| `mail_choice` | `liara_mail`, `own_smtp`, `skip` | `monthly` (toman incl. VAT) |
| `mail_domain` | none (free text: a domain the person owns) | none |
| `wordpress_choice` | `wordpress_app`, `generic_php`, `managed_console` | `managed`, `generic`, `app` (monthly, incl. VAT; `app` = Liara's WordPress image through the API), `marker` (the file that says WordPress) |
| `wordpress_server` | `openlitespeed`, `apache` | `openlitespeed`, `apache` (monthly, incl. VAT), `htaccess` (the project has a .htaccess) |

Plan notes: `storage_wired` {`bucket`, `names`}, `storage_kept`, `mail_wired` {`domain`, `names`}, `mail_own_smtp`, `mail_skipped`, `mail_domain_invalid` {`domain`}. Advice notes (change nothing): `oneclick_available` {`app`, `url`} (Liara has a ready-made app for the same upstream repo: installing it in the console is simpler than building from source), `oneclick_vm` {`app`, `url`} (the ready-made app needs a cloud server), `liara_ai_available` {`base_url`, `url`} (the code uses the OpenAI SDK; Liara AI is OpenAI-compatible), `vm_may_be_needed` (warning) {`signals[]`: `privileged`, `host_network`, `devices`, `udp_ports`, `windows`; `url`} (the app platform cannot give these; a cloud server can). Ready-made apps: plan note `oneclick_plan` {`app`} (installed from its image through Liara's API with the spec's sizes), warning `oneclick_fixed_secret` {`app`, `key`} (the spec ships the same secret to every installer), `plan_ready.how_code` `from_oneclick` {`app`}; API errors `oneclick_unknown` (404), `oneclick_needs_vm` (409). Run outcome: `wordpress_managed` (the person chose Liara's managed WordPress; nothing was created).

`run_finished.follow_ups[]` is what the person still has to do, which the agent cannot: `{code, params, records[]}`. `records` are DNS records to add (`{type, name, value, status?, purpose?}`) when there are any.

| `code` | meaning | `params` |
|---|---|---|
| `mail_dns` | add these records at your DNS provider so the email server can send and receive (SPF, DKIM, MX, return-path CNAME); `status` is Liara's own check | `domain`, `mail` |
| `mail_dev_mode` | the email server was created in Liara's DEV mode: emails stay in its sent folder and are not delivered until the server is switched to live in the console | `domain` |
| `bucket_endpoint_unconfirmed` | the bucket's S3 endpoint could not be confirmed (Liara's API does not give it); check it against the bucket's settings page | `bucket`, `endpoint` |
| `wordpress_console` | create the managed WordPress site in the console | `url` |
| `wordpress_db_import` | import the database dump in phpMyAdmin (no API for it) | `app` |
| `wordpress_permissions` | if pages show permission errors, fix owner and permissions in the console's file manager | `app` |
| `wordpress_upload_failed` | the wp-content upload failed; send it by hand (FTPS or the file manager) | `app`, `why` (a `wp_*` code) |
| `wordpress_nothing_sent` | no theme, plugin or language files were found to send | `why` |

`resource_created.kind` is now `app`, `database`, `bucket` or `mail`; `run_finished.created` also lists `buckets` and `mail_servers`, and a cleanup removes them (a bucket's keys first, an email server's SMTP users first).

## Deploy history and rollback (`/projects/{id}/releases`)
`GET` lists the app's releases newest first: `{app, current, total, releases[{id, state, current, port, type, image, created_at, message, commit, branch, rollback}]}`. `rollback` says how a release can be gone back to: `source` (the agent kept the source it deployed: the same path as a normal deploy, reliable), `image` (Liara's own image of a release the agent did not make: less certain, say so), or `null` (not possible: the current release, a failed one). `POST .../{release}/rollback` with `{confirm: true}` makes it the running release again by creating a new one: `{release_id, state, ok, via, url, serving}`; `serving` is whether the app answered over HTTP afterwards. The last 5 deployed sources are kept per project under `.agent-state/releases/`.



## Live-app diagnosis (`GET /projects/{id}/diagnosis`, chat tool `diagnose_app`)

`state` is `healthy`, `degraded` or `down`. Each cause is `{code, score 0-100, params, evidence[], fixes[]}` and the list is sorted most likely first. The chat model words them for the person; a client that words them itself uses these codes.

Causes: `still_starting` {`minutes`} (a new release answers 5xx but nothing in the logs is wrong), `out_of_memory`, `memory_exhausted` {`ram_gb`, `peak_percent`}, `cpu_saturated` {`peak_percent`}, `bad_release` {`minutes`} (a release made recently, the site fails since), `release_unhealthy` {`state`}, `code_error` (the logs carry an exception), `missing_env` {`names`}, `missing_python_module`, `missing_node_module`, `outbound_blocked`, `db_auth_failed`, `db_localhost`, `wrong_port`, `bound_to_localhost` (the heal loop's own readings of the logs), `slow_response` {`ms`}, `errors_in_logs` (the site answers but errors are logged), `not_serving` {`http`}.

Fixes carry a `code`: `rollback`, `resize_plan`, `set_env`, `wait` (give a new release a few minutes), `fix_code` (read the file the error points to, edit it, release it), `profile` (a slow site with idle resources: look at queries and calls to other services), `optimise_cpu`, `reduce_memory`, `check_port_and_bind`, `read_logs`. A fix with a `tool` and `args` is a change the chat can make as an approval card (`rollback` {release_id}, `resize_plan` {plan}, `set_env`); the others are advice. CPU and memory are read from Liara's metrics and compared with the plan's limits (memory in bytes; the CPU unit is not documented by Liara and is taken as cores).


## Registrar guide (`GET /projects/{id}/domains/guide`)

Steps: `irnic_login` {`url`}, `irnic_domains` {`domain`}, `irnic_manage`, `irnic_dns_section`, `irnic_edit_rows`, `irnic_enter_ns` {`nameservers`}, `irnic_ip_empty`, `irnic_enter_own_ns`, `irnic_secret`, `irnic_apply`, `ns_pending` (the zone's nameservers are not known yet), `reg_login`, `reg_find_ns`, `reg_set_ns` {`nameservers`}, `reg_own_ns`, `records_at_provider` {`domain`, `count`}, `done_nameservers` {`domain`} (Liara already serves the zone), `wait`, `verify`. The IRNIC steps come from two independent public tutorials; the panel's Persian labels are quoted in the wording.

Fixes from the diagnosis panel: `fix_not_allowed` (422: only `rollback`, `resize_plan` and `set_env` can be made from there), `fix_invalid` (409: the change no longer applies, for example the release is already the running one; the message says why).

Advice (`diagnosis.advice[{code, params, fixes}]`, shown while the app still answers): `memory_tight` {`peak_percent`} (70 to 90% of the plan's memory), `cpu_tight` {`peak_percent`}, `a_little_slow` {`ms`}; a fix with a `tool` is the same card as for a cause.

Alerts: when the uptime check sees an app go down, the agent diagnoses it at once and keeps the result (`GET /projects/{id}/diagnosis/latest`). The alert sent to the person's webhook then carries `diagnosis: {state, cause, evidence[], fixes[]}` and its `text` ends with the most likely cause in one English sentence.

Limits (`GET /projects/{id}/limits`): `known` codes `daily_deployments`, `networks_not_deletable`, `tier_fixed_at_creation`, `release_not_cancellable`, `app_names_global`, `logs_lag`, `free_address_expires`, `cpu_unit_unknown` (worded by the client as `lim.known.<code>`). Card warning `deploy_quota` {`used`, `cap`}: only a few of the day's releases are left.

Scheduled health check: the alert webhook gets `event: report` (with the same `diagnosis` object as an outage alert) when a running app is not healthy or has advice; a healthy app is kept silently. `diagnosis.ai` is the model's reading, used only when the rules gave no cause or one below 60: each entry has `confidence` (never above `medium`, and `low` without a real quoted log line) and `grounded`.

## Code problems: Sakoo or your own agent
When a deploy stops because of an error in the developer's code (`diagnosis.category == "code"`), the run asks one question, `code_fix_choice`. The answer sends the run one of two ways:

- **`sakoo`**: rules first (`patch_add_dependency`, `patch_add_npm_dependency`, `patch_node_engine`), then a model reads a copy of the project and proposes `repair_code`. Changes go to Sakoo's copy only and are asked first (`confirm_request.kind == "patch"`), unless the person turned auto-fix on: then `fix_auto_applied` {`code`, `kind`, `text`, `params`} is emitted instead of a card. The diff of every change arrives as `patch_ready` and is kept so the person can open a pull request (`POST /api/projects/{id}/pr`) or download it (`GET /api/projects/{id}/fixes.patch`).
- **`own_agent`**: the run waits. `handoff_waiting` {`id`, `app`, `run_id`, `mcp_url`, `diagnosis`, `expires_at`} tells the page to show how to connect the developer's coding agent (Claude Code, Cursor...) to the MCP server at `mcp_url` with a personal token (`/api/settings/agent-tokens`). Every tool call the agent makes while the run waits appears as `external_agent_call` {`id`, `tool`, `ok`, `detail?`}. The wait ends with `handoff_received` {`id`, `how`: `fix` (files sent), `redeploy` (pushed to Git, newest code released) or `sakoo` (the person chose Sakoo instead: `POST /api/runs/{id}/handoff/sakoo`), `files`}, after which the same run releases the app and checks it. After 30 minutes without an answer the wait ends with `request_expired` {`request`: `handoff`} and the failure is reported as it is.

The answers ending in `_remember` store the choice on the project (`ProjectRow.code_fix_choice`; change it with `PUT /api/projects/{id}/code-fix-choice`), and the question is no longer asked.
