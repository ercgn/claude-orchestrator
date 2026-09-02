# claude-orchestrator

`orchestrator` is a Claude Code plugin that turns a Fable 5.x session into an
orchestrator: you plan, route, and judge, while nine model-bound role agents do
the reading, the writing, and the verification. It ships the `/orchestrate`
skill, the nine role agents, and four hook entries across three events that
activate the mode automatically and enforce the routing rule mechanically. On
any non-Fable model it stays out of your way.

## Why it exists

The routing rule — never let a subagent inherit your frontier-tier session
model — is easy to state and easy to forget. An unrouted workflow script once
spawned 118 subagents on the session's Fable model, every one of them running at
full cost and full tool access, because a single `agent()` call named neither a
role nor a model. Prose in a skill file cannot stop that; a PreToolUse hook can.
This plugin makes the rule mechanical, so the expensive mistake is denied before
anything runs instead of being noticed on the bill.

## What you get

**The skill.** `/orchestrator:orchestrate` — the phase-aware orchestration
lifecycle: dispatch brakes, an approval gate, evidence discipline, bounded
recovery, and fresh-context verification.

**Nine role agents.** Each one carries its own model binding in its frontmatter,
which is why you route by role name and never pass a `model` argument.

| Role | Model | Effort | Purpose |
|---|---|---|---|
| `orchestrator:scout` | haiku | — | Read-only reconnaissance: where/how is X, symbol usages, config values |
| `orchestrator:Explore` | sonnet | low | Read-only broad sweeps across many files, directories, and naming conventions |
| `orchestrator:classifier` | haiku | — | High-volume labeling, tagging, triage, and extraction against a fixed taxonomy |
| `orchestrator:plan-verifier` | opus | high | Read-only review of one plan envelope or slice; returns bare `READY` or structured `REVISE` |
| `orchestrator:security-reviewer` | opus | high | Read-only security evidence and threat review, before approval |
| `orchestrator:mech-executor` | sonnet | high | Fully-specified mechanical work: pattern refactors, convention tests, docs, bulk edits |
| `orchestrator:executor` | opus | xhigh | Implementation needing judgment: features, bug fixes, design-sensitive refactors |
| `orchestrator:verifier` | opus | high | Fresh-context outcome verification; returns CONFIRMED / REFUTED / INCONCLUSIVE and never fixes |
| `orchestrator:security-executor` | opus | xhigh | Approved security implementation, deliberately kept off Fable |

`—` means the agent file sets no `effort:`, so the harness default applies.

**Four hook entries across three events**, all python3 stdlib handlers in `hooks-handlers/`.

| Event | When it fires | What it does | Minimum build |
|---|---|---|---|
| `SessionStart` | session start, resume, compact | Injects the activation context when the model is known to be Fable; a conditional context when the model is not yet known; one standby notice the first time a non-Fable model is seen | 2.1.224 |
| `UserPromptSubmit` | every prompt you send | Re-injects a one-line reminder while the session model is Fable; otherwise silent | 2.1.224 |
| `PostModelSwitch` (opt-in) | after `/model` changes the session model | Not registered by default — see "Optional: model-switch tracking on 2.1.258+" below | 2.1.258 |
| `PreToolUse` on `Workflow` | before a workflow script runs | Denies a script containing a bare `agent()` call, or a call naming an `orchestrator:` role together with a `model` | 2.1.224 |
| `PreToolUse` on `Agent` | before an Agent call | Denies a named role given a `model` (R1); on a known-Fable session, denies a session-model-inheriting built-in invoked with no explicit model (R2) | 2.1.224 |

Hooks are harness-only: they cost no model context except the short activation
or reminder text they inject.

## Requirements

- Claude Code 2.1.224 or newer, with plugin support and the `Workflow` tool.
  All registered hooks work on 2.1.224+.
- `python3` on `PATH` (standard library only — no packages to install).
- Access to a Fable 5.x model. Without one the plugin installs cleanly, prints one standby line, and otherwise stays quiet; the guards remain armed.

## Install

**A. Clone into your skills directory** (simplest — Claude Code loads plugins
found under `<config>/skills/*`):

```bash
git clone git@github.com:ercgn/claude-orchestrator.git ~/.claude/skills/orchestrator
```

The repository is private; ask the owner to add you as a collaborator first.

Then run `/reload-plugins` in open sessions, or start a new session.

**B. Marketplace:**

```bash
claude plugin marketplace add ercgn/claude-orchestrator
claude plugin install orchestrator@claude-orchestrator --scope user
```

**C. Trial run, no install:**

```bash
claude --plugin-dir /path/to/claude-orchestrator
```

## Set your session model

The plugin cannot set the session model — it only reacts to it. Pin Fable 5.1
any of these ways:

```json
{ "model": "claude-fable-5-1[1m]" }
```

in `~/.claude/settings.json`, or:

```bash
claude --model claude-fable-5-1
```

or `/model claude-fable-5-1` inside a session. The `[1m]` suffix selects the
1M-context variant; the plain id works too. `./install.sh --set-model` makes the
settings.json edit for you, with a timestamped backup.

## Verify

```bash
claude plugin list
claude plugin details orchestrator
```

`plugin details` should report `Skills (1)`, `Agents (9)`, and
`Hooks (3)  SessionStart, UserPromptSubmit, PreToolUse`.

On your next Fable session start you should see a line like:

```text
orchestrator: active (claude-fable-5-1)
```

On a non-Fable model you get the one-time standby line instead, and nothing
after that:

```text
orchestrator: installed — standby on claude-sonnet-5. Orchestrator mode activates on Fable 5.x ...
```

While the model is still unknown (typically the first turn of a session), the plugin also injects a short conditional context for the model; you do not see it.

To watch a guard fire without risk, ask for a workflow whose script contains a
single bare `agent()` call. The call is denied at the PreToolUse boundary, so
nothing runs and no subagent is spawned:

```text
Blocked by orchestrator: this workflow script has agent call(s) that break the routing rule ...
```

## How the hooks decide

Detection order for "what model is this session on":

1. The `model` field of the `SessionStart` payload, when the build sends one.
2. Otherwise the newer of: the recorded session state (written by a model switch
   or an earlier hook) and the last main-thread assistant message in the
   transcript.
3. Otherwise unknown.

Known limitations:

- (a) The transcript flushes lazily, so the first tool calls of a fresh session
  may run before the model is known. R2 does not fire then; the conditional
  activation context still tells the model to use the roles.
- (b) A mid-session `/model` switch is noticed only after the next reply is
  written to the transcript, unless you enable the optional PostModelSwitch
  hook (2.1.258+ only).
- (c) The Workflow guard checks literal text, so options passed via a spread or
  a variable must inline `agentType`.
- (d) The Workflow guard scans code only: prose inside string literals and comments is ignored, while code inside `${…}` template interpolations is scanned like any other code. Regex literals are not tokenized, so a regex containing `agent(` is reported as a call and a `}` inside a regex within an interpolation ends the interpolation early — both are theoretical. Quoted keys are recognized by text (`"model":`), so a ternary that happens to yield the string `"model"` or a comment placed between a quoted key and its colon can confuse the classification; both are theoretical.
- (e) A plugin cannot shadow the built-in `Explore`. For belt-and-braces, keep a
  copy of `agents/Explore.md` in `~/.claude/agents/` so a bare `Explore` also
  routes to Sonnet.
- (f) Claude Code sets `CLAUDE_PLUGIN_DATA` per plugin, so state (session model records and the standby sentinel) lives under `~/.claude/plugins/data/orchestrator/orchestrator/` for an installed plugin, or `~/.claude/plugins/data/orchestrator-inline/orchestrator/` when loaded with `--plugin-dir`; only if that variable is unset does it fall back to `~/.cache/claude-orchestrator/orchestrator`.
- (g) Scripts over 128 KB or with more than 200 agent calls are denied outright, and any scan that would exceed 2.5 seconds is cut short and denied, so the hook can never hit its 5-second timeout (a timed-out hook would let the call through).

## Optional: model-switch tracking on 2.1.258+

Claude Code 2.1.258 and newer fire a `PostModelSwitch` hook after `/model`
changes the session model. The handler `hooks-handlers/model-switch.py` is
shipped and tested but not registered, because 2.1.224 rejects a hooks file
that contains an event it does not know — which would silently disable every
hook in this plugin on that build. Enable it only if every Claude Code build
you use is 2.1.258 or newer.

To enable, add this entry inside the `"hooks"` object of `hooks/hooks.json`
and run `/reload-plugins`:

```json
"PostModelSwitch": [
  { "hooks": [ { "type": "command", "command": "python3 \"${CLAUDE_PLUGIN_ROOT}/hooks-handlers/model-switch.py\"", "timeout": 5 } ] }
]
```

If `claude plugin list` afterwards shows `loaded with errors` mentioning
`PostModelSwitch`, your build is too old — remove the entry again.

## Customizing

- **Change a role's model:** edit `model:` in that agent's frontmatter. Nothing
  else references it.
- **Add a role:** add `agents/<name>.md`, add a row to the roles table in
  `skills/orchestrate/SKILL.md`, and add the name to `ROLES` in
  `hooks-handlers/common.py` so the Workflow guard's message lists it.
- **Turn it off without uninstalling:**

  ```bash
  claude plugin disable orchestrator@skills-dir
  ```

Run `python3 tests/run.py` after any change to the handlers.

## Uninstall

```bash
rm -rf ~/.claude/skills/orchestrator
```

or `claude plugin uninstall orchestrator` if you installed through a
marketplace. Optionally remove the state directory:

```bash
rm -rf ~/.claude/plugins/data/orchestrator ~/.claude/plugins/data/orchestrator-inline ~/.cache/claude-orchestrator
```

## For the original author: cutover checklist

Once the plugin is installed, remove the loose user-level copies so behavior
comes from exactly one place:

- Delete `~/.claude/skills/orchestrate/`.
- Delete the nine role files in `~/.claude/agents/` — except keep
  `Explore.md` if you want the belt-and-braces override described in limitation (e) above.
- Remove the two orchestration hook entries from `~/.claude/settings.json`: the
  `UserPromptSubmit` echo and the `PreToolUse` Workflow hook (the plugin's
  Workflow guard supersedes `~/.claude/hooks/check-workflow-routing.py`).

Leaving both in place is harmless but noisy: two hooks deny the same call and
only one reason reaches the model.

## License and attribution

MIT — © 2026 Eric Gan. See `LICENSE`.

The orchestration model is adapted from
[pilotfish](https://github.com/Nanako0129/pilotfish) by Nanako0129, also MIT;
its license text is reproduced in `LICENSE`.
