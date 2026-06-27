# Claude Agent Guardrails

Follow [AGENTS.md](AGENTS.md). In particular, keep `mission` OSS-portable:

- do not bake personal/local skills into built-in specialist defaults;
- model private integrations through registries or manifests;
- keep external specialists as evidence providers, not final judges;
- use neutral fixture provider names in tests and public examples.
- treat version bumps as incomplete until the matching remote tag and GitHub Release have both been created and verified.
