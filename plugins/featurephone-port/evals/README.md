# Trigger evaluations

Eight sibling skills share one problem domain, and only one thing decides which of them a user's
sentence reaches: the `description` in each `SKILL.md` frontmatter. It is the only text loaded at
startup. If two descriptions overlap, one steals the other's traffic and the user lands in a skill
that will confidently do the wrong phase's work.

These files exist to prove that does not happen. **Write and run them before rewriting a
description, and again after.**

## Files

| file | skill under test |
|---|---|
| `port-triage.json` | `port-triage` |
| `archive-prep.json` | `archive-prep` |
| `emulator-core.json` | `emulator-core` |
| `guest-re.json` | `guest-re` |
| `android-host.json` | `android-host` |
| `save-integrity.json` | `save-integrity` |
| `server-revival.json` | `server-revival` |
| `port-shipping.json` | `port-shipping` |

## Format

```json
{
  "skill": "emulator-core",
  "evals": [
    {
      "prompt": "my game boots to a black screen and there's no error",
      "expect": "trigger",
      "why": "Failure #1 of six: wall-clock host now(). Silence with no error is the diagnostic."
    },
    {
      "prompt": "the apk won't install on my friend's phone",
      "expect": "no-trigger",
      "why": "port-shipping. Signing, ABI or minSdk."
    }
  ]
}
```

- `prompt` — what a real user types. **Symptoms, not jargon.** "the minimap keeps getting clicked
  by itself", not "hit-test chain ordering defect". Lowercase, unpunctuated and slightly rambling
  is more representative than a clean sentence, and it is what the description has to survive.
- `expect` — `"trigger"` or `"no-trigger"` for **this** skill.
- `why` — the mechanism, and for a `no-trigger` case, **which sibling should have won it**. The
  `why` field is what makes a failed case actionable: it tells you whether to widen the intended
  owner's description or narrow the thief's.

Every file has at least three of each. Some carry an extra case labelled **AMBIGUITY PROBE** in
its `why` — a prompt that two skills can legitimately claim. Those are not pass/fail on the
landing skill; they pass when whichever skill lands hands off cleanly, and fail when it silently
does the other phase's work. `port-triage.json`'s first case is the deliberately ambiguous one
that must land on the router.

## Running them

### Automated: `skill-creator`

```
/plugin install skill-creator@claude-plugins-official
```

It runs each case in an isolated subagent, produces a grading report and a with-skill versus
without-skill benchmark (pass rate, tokens, wall time), does **blind A/B between two versions of a
skill**, and — the part that matters most here — **generates additional should-trigger and
should-not-trigger prompts and tunes the description against them**.

`skill-creator` expects a single `evals/evals.json` per skill directory. To use it against one of
these files, copy it to the skill's own directory under that name:

```bash
cp evals/emulator-core.json skills/emulator-core/evals/evals.json
```

Then merge any prompts it generates back into the per-skill file here, with a `why`. Keeping the
canonical copies in one directory is what makes the cross-skill matrix below possible; the
per-skill copy is a working file, not the source of truth.

### Manual baseline

The automated harness measures whether a skill *helps*. It does not, on its own, prove that eight
descriptions *partition* the space. For that, run the matrix by hand.

**With the plugin loaded**, from a clone of the marketplace repo:

```bash
claude --plugin-dir ./plugins/featurephone-port
```

Paste one prompt into a **fresh session** — one prompt per session, no history, since a prior turn
about the archive will drag the next prompt toward `archive-prep` and invalidate the result.
Record which skill fired, or that none did.

**Without the plugin**, run the same prompt in a session where the plugin is absent or disabled
(omit `--plugin-dir`, or disable it with `/plugin`). This is the baseline: some prompts get a good
answer with no skill at all, and a skill that fires but does not beat the baseline is costing
tokens for nothing.

### The cross-skill matrix — the test that actually matters

Take **every** prompt from **all eight** files (112 as of 0.2.1), run each one once, and build a
matrix of prompt → skill that fired.

Pass conditions:

1. Every `"trigger"` prompt fires its own skill.
2. Every `"no-trigger"` prompt fires the sibling named in its `why` — **not merely "not this
   skill"**. A prompt that fires nothing is also a failure: the user gets no methodology at all.
3. No prompt fires two skills' worth of behaviour in one turn.
4. Ambiguity probes land on one of their two legitimate owners and hand off rather than
   improvising the other phase.

Failures and their fixes:

| failure | fix |
|---|---|
| A `trigger` case fires nothing | The description is missing the user's actual words. Add the literal symptom text — the panic message, the error string, the plain-English complaint. |
| A `trigger` case fires a sibling | The sibling's description is too broad. Narrow the sibling first; widening the intended owner just makes both greedy. |
| A `no-trigger` case fires this skill | This description is over-claiming. Usually a generic verb ("build", "fix", "find") with no domain qualifier. |
| Everything routes to `port-triage` | The router is over-claiming. It should own *orientation*, not any symptom that is already specific. |
| Nothing ever routes to `port-triage` | The phase skills have eaten the ambiguous space. Check the first case in `port-triage.json` specifically. |

## Test with more than one model

**The router is the component most sensitive to model choice.** Routing is a judgement call over
eight similar descriptions, and a smaller model leans harder on literal keyword overlap while a
larger one infers intent — so a description that partitions perfectly under one model can collapse
into "everything goes to the router" or "everything goes to the biggest description" under
another.

Run the full matrix on at least the small, mid and large models available to you. Where they
disagree, tune for the **smallest** model you expect users to run: literal trigger words fix
small-model routing without harming large-model routing, whereas descriptions that rely on
inference only work upward.

## Budget note

The eight descriptions are always in context. Keep each under 1024 characters — a hard cap in the
portable Agent Skills spec — and put the key use case **first**, because the listing text is
truncated. Every trigger word you add is a permanent cost paid by every session, including the
ones that never touch this plugin: add words that a real user types, and delete words that only
an author would.

## When to re-run

- Any change to any `description`. Always the whole matrix, never just the edited skill — the
  eight descriptions compete, so narrowing one changes where its neighbours' traffic goes.
- Adding or splitting a skill.
- Before a release that bumps `MINOR` or `MAJOR`.
- After a user reports landing in the wrong phase. Add their exact sentence as a new case with a
  `why` first, then fix the description; the regression case is the durable part.
