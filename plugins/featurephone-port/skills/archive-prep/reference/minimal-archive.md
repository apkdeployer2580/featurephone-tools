# The minimal bootable archive

What is actually required, proven by deletion; how to synthesize a descriptor when the dump lost one; and how to use minimal archives as a bisection instrument.

Tags: **(A)** generic · **(B)** worked example from the reference port · **(C)** do not copy.

## Contents

1. [The measured minimum](#1-the-measured-minimum)
2. [The synthesized descriptor](#2-the-synthesized-descriptor)
3. [Choosing the three values](#3-choosing-the-three-values)
4. [PID is permanent](#4-pid-is-permanent)
5. [The experiment log](#5-the-experiment-log)
6. [Bisection method](#6-bisection-method)
7. [Per-platform minima](#7-per-platform-minima)
8. [What cannot be synthesized](#8-what-cannot-be-synthesized)
9. [Do not ship a minimal archive](#9-do-not-ship-a-minimal-archive)

---

## 1. The measured minimum

**(B) A KTF archive boots with two entries and nothing else:**

```
__adf__          the app descriptor
<AID>.jar        the app
```

Deleting the carrier catalog blob and all three icon entries from a known-good archive produced a two-entry zip that booted 120 frames clean:

```
[probe] loaded
[work] f=100 instr=185823685 (1.9 M/frame) ... virt 0.64x
```

**(B) Independently confirmed by grep:** searching the entire emulator tree, the Android host and the probe crate for the catalog-blob name and the icon extension returns **zero hits**. Nothing reads them. §1 of [archive-anatomy.md](archive-anatomy.md) lists what the loader does read; the answer is three descriptor keys and two entry names.

**(A) Practical use.** The minimum is not a shipping format — it is a *fact you can lean on*. When an archive misbehaves you can delete everything unread and still have a booting artifact, which turns "something in this 1.8 MB zip is wrong" into a two-variable problem.

## 2. The synthesized descriptor

**(B) A hand-written three-line descriptor works.** 37 bytes, LF-terminated:

```
PID:PD00nnnn
AID:0A1B2C3D
MClass:Clet
```

An archive containing only that descriptor and the jar booted identically to the original — same gauge, same frames. Everything else the carrier put in the descriptor (`Name`, `Ver`, `Vdr`, `DisplaySize`, icon filenames) is metadata no code path reads.

Write it with **LF only**, no trailing `\r`, no BOM. A BOM is worse than CRLF: it makes the *first* line's key unrecognisable, so the parser silently leaves that field empty and you get an empty-AID `NotFound` with no obvious cause.

```python
adf = b"PID:%s\nAID:%s\nMClass:%s\n" % (pid, aid, mclass)
assert adf.count(b"\r") == 0 and not adf.startswith(b"\xef\xbb\xbf")
```

## 3. Choosing the three values

**(A) AID — free, but it must match the jar entry.**

If you are synthesizing, the easiest correct choice is to read the existing jar entry name and strip `.jar`. If the jar has some other name, you may rename either side as long as both agree — the AID carries no semantics beyond the lookup. Keep the carrier's original 8-hex shape if you have it; it costs nothing and keeps the artifact recognisable to other tooling.

**(A) MClass — not free, and not derivable from the jar.**

For an AOT title there are no `.class` files to enumerate. Sources, in order:

1. The original descriptor, if any copy of it survives anywhere (some dumps store it as a loose file beside the zip).
2. Any sibling title from the same publisher — **(B)** both titles examined in the reference series used the platform's conventional applet class name, unchanged.
3. The guest image itself: dump strings and look for short class-name-shaped literals near the platform's own runtime class names. Then just try them — a wrong `MClass` fails fast and unambiguously ([fault-dictionary.md](fault-dictionary.md#4-f2-wrong-or-misspelled-mclass)).

**(A) PID — free to choose, impossible to change later.** See §4.

## 4. PID is permanent

The PID is the on-device save namespace. The database path is `<root>/<pid>/db/<name>/<record>`.

**(B)** A probe run against an empty root created `work/db/PD00nnnn/db/<SaveName>/1`.

**Change the PID after a release and every user's saves become unreachable.** The loader has no migration path and will not look in the old directory. There is no warning; the game simply starts as if new.

So: decide the PID at step 3 of the skill, write it into the port's notes next to the AID and the MClass, and treat it like the `applicationId` — a permanent identifier, not a configuration value. If you are synthesizing rather than inheriting, prefer the carrier's original PID when you can recover it; otherwise pick something stable and title-specific and never touch it again.

**Related trap.** The record *name* the guest passes can contain path-ish characters — **(B)** an observed record name began with `./`. Your host filesystem layer must sanitise ids and reject traversal **without changing the effective path**, or the probe's database and the device's database will diverge and you will "lose" saves that are simply in a differently-spelled directory.

## 5. The experiment log

**(B)** Six single-variable mutations of one known-good archive. Reproduce this table for your own title on day one; it takes about twenty minutes and it pays for itself the first time boot breaks.

| archive | mutation | result |
|---|---|---|
| `minimal.zip` | keep only `__adf__` + `<AID>.jar` | **boots**, 120 frames clean |
| `synth.zip` | replace the descriptor with the 3-line one, 37 B | **boots**, identical |
| `crlf.zip` | descriptor LF → CRLF, nothing else | `NotFound` panic in `java/io/file.rs`, after `loaded` |
| `badclass.zip` | `MClass:` misspelled | `Option::unwrap()` on `None` in `jvm/src/value.rs` |
| `noadf.zip` | delete `__adf__` | `not a KTF archive`, clean, at load time |
| `nobss.zip` | rename `client.bin<N>` → `client.bin` | `FatalError("Invalid bss_size in filename ...")` |

**(B) A prior, real bisection** — the reference port's own working directory, read as a chronology, shows the same method applied under pressure: raw dump → "saves moved to the archive root" → "saves removed" (both still CRLF, both still broken) → "CRLF→LF" (**first bootable**) → final. Four archives, one variable each. The two save-related attempts were the wrong hypothesis; keeping them as separate files is what made that visible.

## 6. Bisection method

**(A)** When an archive will not boot and the fault dictionary does not match the symptom:

1. **Reduce to the minimum.** Keep only the descriptor and the jar. Still broken? The fault is in one of those two, and you have eliminated everything else.
2. **Replace the descriptor with the synthesized three-liner**, reusing the AID from the jar entry name. Now boots? The original descriptor was malformed — diff the two, and check CR bytes, BOM and trailing whitespace first.
3. **Still broken?** The fault is inside the jar. Check the native member name and its digits, then that the jar itself opens as a zip (a truncated dump is not exotic).
4. **Add the deleted entries back one at a time** only if you need to know which mattered. Usually you do not — they are unread.

Keep every intermediate as a separate file with a name that states the hypothesis. Do not mutate in place; the point of the method is that each artifact remains re-runnable.

## 7. Per-platform minima

**(A)** The two-entry minimum is a KTF result. What generalises is the *shape* of the question — "which entries does the detector and the loader actually name?" — not the answer.

| platform | detector needs | loader additionally needs | descriptor synthesizable? |
|---|---|---|---|
| KTF | `__adf__` | `<AID>.jar`, `client.bin<N>` at the jar root | **yes**, 3 lines, proven |
| LGT | `app_info` | the app jar, `binary.mod` inside it | likely — same three-key line scanner, same no-trim parse; **unverified** |
| SKT | any `*.msd` | per the `.msd` properties | parser trims and reads generic `key: value` properties, so it is more forgiving; **unverified** |
| J2ME | `.jad`, or a `MIDlet-1` manifest attribute | standard MIDP jar | the manifest is inside the jar and self-describing; a missing `.jad` is usually survivable |

The LGT and SKT rows are inferences from reading their loaders, not measurements. If you are on those platforms, run §5's experiment table yourself and correct this file.

## 8. What cannot be synthesized

- **The jar.** Obviously — but state it, because "minimal archive" invites the idea that a stub might boot far enough to be useful. It does not: the loader reaches the guest image within milliseconds of `loaded`.
- **The native member's BSS digits.** They are a real allocation size the guest depends on. If the digits are lost, they cannot be recovered from the file alone — you would have to derive the guest's BSS requirement from its own relocation code. Do not guess a round number.
- **A correct `MClass` with no evidence.** Guessing is legitimate (the failure is fast and unambiguous) but *inventing* one is not; there is a finite set of plausible names and they come from the platform or from the image's strings.

## 9. Do not ship a minimal archive

The minimum is a diagnostic instrument, not a distribution format.

- The entries you deleted are the publisher's artwork and catalog metadata. Deleting them does not make the remaining 1.8 MB any less licensed, and a stripped archive is still not yours to publish.
- Shipping a reduced archive discards evidence about the original package that you may want later.
- The one deletion that *does* belong in the shipping archive is `P/*` — the previous owner's private data. Everything else stays.

Ship the repaired, name-reconciled, privacy-stripped archive from the skill's step 4 — and ship it into your own build only, never into a repository.
