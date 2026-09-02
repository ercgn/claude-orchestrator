# Changelog

## 4.0.0

First release as an installable Claude Code plugin. Previously this lived as
loose files under `~/.claude` (skill version 3.2.0), which meant it could not be
shared and drifted between machines.

**Added**

- Packaged as the `orchestrator` plugin: the `/orchestrator:orchestrate` skill
  plus nine role agents, each keeping its own `model:` frontmatter binding.
- Four registered hook entries across three events (SessionStart,
  UserPromptSubmit, PreToolUse on Workflow and on Agent), plus an opt-in
  PostModelSwitch handler for 2.1.258+ (shipped and tested, not registered by
  default because 2.1.224 rejects unknown hook events and would then load
  zero hooks), all implemented as python3 stdlib handlers under
  `hooks-handlers/`:
  - `SessionStart` — injects the orchestrator activation context when the
    session model is known to be Fable, a conditional context when it is not yet
    known, and a one-time "installed — standby" notice on a non-Fable model.
  - `UserPromptSubmit` — re-injects a short reminder while the session model is
    Fable.
  - `PostModelSwitch` (opt-in, 2.1.258+) — records a mid-session model change
    and acknowledges activation or standby; not registered in
    `hooks/hooks.json` by default.
  - `PreToolUse` on `Workflow` — denies a script containing a bare `agent()`
    call, or a call that names an `orchestrator:` role together with a `model`.
  - `PreToolUse` on `Agent` — denies an invocation-level `model` on a named
    `orchestrator:` role (R1), and, on a known-Fable session, denies the
    session-model-inheriting built-in agent types invoked with no explicit
    model (R2).
- Model-state tracking: per-session state under
  `$CLAUDE_PLUGIN_DATA/orchestrator` (or `~/.cache/claude-orchestrator/`),
  resolved from the SessionStart payload, a model switch, or the newest
  main-thread assistant entry in the transcript, whichever is newer.
- `tests/run.py` — an offline suite covering both guards, all three
  context-injection handlers, and the shared model-detection helpers.
- `install.sh` — optional helper for prerequisite checks, manifest validation,
  and pinning the session model.
- Workflow guard scans code inside template-literal `${…}` interpolations (a bare call there was previously missed).
- Workflow guard: quoted property keys (`{"agentType": …}`, `{'model': …}`) are
  normalized to bare keys of identical length before tokenizing, so quoting a key
  no longer hides it from the scanner.
- Workflow guard: a nested `agent()` call is masked out of its enclosing call's
  span, so `agent(agent(0, {model: 0}), {})` is now caught as a bare outer call
  instead of borrowing the inner call's `model`.
- Workflow guard: the `agentType: 'orchestrator:…'` role test now runs on a
  comments-blanked, strings-kept view, so an `agentType:` mentioned inside a
  comment no longer turns a legitimate ad-hoc `model` call into a denial.
- Workflow guard: parenthesis matching is one linear left-to-right pass instead of
  a per-call scan to end-of-file (10,000 `agent(` tokens took 11.9 s, past the 5 s
  hook timeout — and a timed-out PreToolUse hook lets the call proceed). Scripts
  over 512 KB or with more than 500 agent calls are now denied outright rather
  than scanned, and the listed violations are capped at 20 lines plus a
  `(+N more)` tail so the deny payload stays small.
- Workflow guard: a hard 2.5 s scan budget, checked after each whole-script pass
  and at every per-call iteration. A scan that runs past it stops where it is and
  denies, so the guard can never reach the 5 s PreToolUse timeout — a timed-out
  hook is treated as absent and the call proceeds. The caps behind it are
  tightened from 512 KB / 500 calls to 128 KB / 200 calls: a 524,288-character
  script with 500 nested calls took 6.9 s to scan and was then allowed through.
  The call cap is now counted on the comments-blanked view, so `agent(` tokens
  written in a comment no longer count toward it, while the character cap is
  still measured on the raw script, before any tokenizing.
- Model state: a transcript-sourced record is rewritten only when the model
  actually changes, and it stores the transcript entry's own timestamp rather
  than the current time, so a stale decision can no longer outlive a newer
  transcript entry.
- classifier agent: removed the invalid `effort: default` frontmatter value that Claude Code warns about at load time.

**Changed from the user-level 3.2.0 skill**

- Every role is referenced by its namespaced name (`orchestrator:scout`,
  `orchestrator:executor`, …). Bare role names do not resolve for a plugin.
- The roles table now lists nine roles; `classifier` was previously shipped as
  an agent file without a table row.
- A plugin agent cannot shadow a built-in, so `orchestrator:Explore` no longer
  overrides the built-in `Explore`. The Agent guard's R2 rule replaces that
  override on Fable sessions.
- Cross-vendor verification no longer pins a specific Codex model and is
  described as optional, since the `/codex` skill is not part of this plugin.
- The design-tooling paragraph in the skill is marked optional for the same
  reason.
