# english-coach — Quick Reference

English learning skill for IT engineers targeting native-level communication.

## Commands

| Command | What it does |
|---------|-------------|
| `/english-coach` | Today's practice menu (2–3 challenges) |
| `/english-coach write <text>` | Native-style writing review |
| `/english-coach write <topic>` | Get a writing prompt, then review |
| `/english-coach speak <phrase>` | Pronunciation breakdown + kana guide |
| `/english-coach native <scene>` | Natural IT expressions for a scene |
| `/english-coach grammar <question>` | Grammar explained through images |
| `/english-coach vocab <word>` | Deep word dive — scene → examples → definition |

## Philosophy

- **Image first, Japanese last** — every explanation starts with a visual/scene
- **"Why do natives say this?"** — feel the logic, don't memorize rules
- **Produce English** — every session ends with you writing or saying something

## File Structure

```
english-coach/
├── SKILL.md                     ← Main skill definition
├── README.md                    ← This file
└── references/
    ├── writing.md               ← Writing review checklist, format templates
    ├── speaking.md              ← Connected speech, reductions, IT pronunciation
    ├── grammar.md               ← Tense, articles, modals, prepositions
    ├── vocabulary.md            ← Phrasal verbs, idioms, collocations
    └── it-context.md            ← IT Slack/PR/standup language
```

## Obsidian Save Paths

Results are saved to your `02_English/` vault:

| Mode | File |
|------|------|
| write | `1_Writing/YYYY-MM-DD-<topic>.md` |
| speak | `Speaking(Intnation)/Intonation.md` (append) |
| native / IT | `2_Grammer(Vocablary)/native-IT.md` (append) |
| grammar | `2_Grammer(Vocablary)/grammar-notes.md` (append) |
| vocab | `2_Grammer(Vocablary)/vocab-IT.md` (append) |

## Examples

```
/english-coach
→ Today's 3 practice challenges

/english-coach write "Please review the PR when you have time."
→ Native feedback + rewritten version

/english-coach speak "I should've told you earlier"
→ Breakdown: "shoulda told ya earlier" | シュダ・トウジャ・アーリャ

/english-coach native slack
→ 5 natural Slack phrases for daily use

/english-coach grammar "have done vs did"
→ Scene: "a bridge to now" vs "a sealed box"

/english-coach vocab "figure out"
→ Image: tangled knots → you work through until straight
```
