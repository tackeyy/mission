# Oracle Command Provider Safe Defaults

`oracle-reviewer` is an optional command provider. It may use an external browser or API, so its wrapper must make approval boundaries explicit instead of relying on an already signed-in browser profile by default.

## Consent Scopes

Keep these approvals separate in the mission confirmation text:

1. External send: send selected prompt, artifacts, or repository context to Oracle/ChatGPT.
2. Browser session material: reuse cookies, profile copies, or existing authenticated browser state.
3. Paid quota: consume paid API or model quota.

External-send approval does not authorize browser session material reuse. First-use provider consent also does not authorize browser session material or paid quota.

## Safe Local Defaults

Local `oracle-reviewer` wrappers should default to manual login or an `awaiting-input` result, not profile copying.

```sh
ORACLE_MISSION_MODE=cli
ORACLE_MISSION_ENGINE=browser
ORACLE_MISSION_MODEL=gpt-5.5-pro
ORACLE_MISSION_BROWSER_MANUAL_LOGIN=1

# Intentionally unset by default.
# ORACLE_MISSION_COPY_PROFILE=
# ORACLE_MISSION_COPY_PROFILE_APPROVED=0
```

Only pass Oracle CLI `--copy-profile` when both are true:

- `ORACLE_MISSION_COPY_PROFILE` points at a user-approved browser profile for this run.
- `ORACLE_MISSION_COPY_PROFILE_APPROVED=1` is set after the browser-session-material risk is disclosed.

If a wrapper sees `ORACLE_MISSION_COPY_PROFILE` without approval, it should return an `awaiting-input` result and exit with a configured awaiting-input code, such as `75`.

```text
approval required: browser session material consent is required before using ORACLE_MISSION_COPY_PROFILE
```

Pair the provider registry with a result contract:

```yaml
result_contract:
  min_non_template_chars: 200
  forbidden_markers:
    - "Oracle Browser Review Prepared"
    - "Browser Review Prepared"
  awaiting_input_markers:
    - "approval required:"
  awaiting_input_exit_codes: [75]
```

This keeps optional Oracle evidence visible without treating a browser/session approval blocker as a completed review or a generic provider failure.
