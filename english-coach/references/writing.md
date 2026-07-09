# Writing Reference — English Coach

> **Goal**: Write like someone who grew up speaking English, not like someone who translated from Japanese.

---

## Writing Review Checklist (10 points natives check)

When reviewing any written English, scan for these in order:

1. **Contractions** — Did you use them where a native would? ("I'm" not "I am" in casual Slack)
2. **Subject clarity** — Is the subject of every sentence immediately clear?
3. **Verb choice** — Is the verb the most natural one? (natives say "fix the bug" not "repair the bug")
4. **Article accuracy** — Is "a/an/the/∅" used naturally? (hardest for Japanese speakers)
5. **Preposition fit** — Does the preposition *feel* right? ("working on" not "working to" a task)
6. **Sentence length** — Are sentences too long? Split at natural breath points.
7. **Tone match** — Does tone fit the context? (Slack = casual; PR desc = professional but friendly)
8. **Redundancy** — Remove "please be advised that", "I would like to", "as you know"
9. **Hedging** — Add "might", "could", "I think" where appropriate to avoid sounding blunt
10. **Ending** — Does it end with a clear call-to-action or next step?

---

## Format-Specific Templates

### Slack Message

**Asking for help:**
```
Hey [name], quick question — [concise question]?
```

**Reporting progress:**
```
Just pushed [what you did]. Still working on [what's next] — should be done by [time].
```

**Flagging a blocker:**
```
Heads up: I'm blocked on [issue]. [Brief reason]. Anyone have context on this?
```

**Reacting / Acknowledging:**
```
Got it, thanks!
Makes sense, I'll [action].
On it — I'll update you by [time].
```

---

### Email (Professional)

**Subject line**: Be specific. "Question about auth flow" > "Question"

**Opening**: Skip "I hope this email finds you well." Start with the point.
```
I'm reaching out about [topic].
Following up on our conversation about [X]...
Quick question regarding [Y]:
```

**Body**: One idea per paragraph. Use bullets for multiple items.

**Closing**:
```
Let me know if you have any questions.
Happy to jump on a call if that's easier.
Looking forward to hearing from you.
```

---

### PR Description

```markdown
## What
[One sentence: what does this PR do?]

## Why
[One sentence: why is this needed?]

## How
- [Key implementation decision 1]
- [Key implementation decision 2]

## Testing
- [ ] Unit tests added/updated
- [ ] Manual testing: [steps]

## Notes
[Anything reviewers should pay special attention to]
```

---

### Code Comment

**Inline comment** — Explain *why*, not *what*:
```js
// Skip validation here — the API guarantees this value is always a positive int
// Using Map instead of object for O(1) lookup at scale
```

**Function docstring** (when needed):
```ts
/**
 * Retries the fetch with exponential backoff.
 * @param maxRetries - defaults to 3; set to 0 to disable
 */
```

---

## Common Japanese → Unnatural English Patterns

| Japanese Thought | Literal (Unnatural) | Native English |
|-----------------|---------------------|----------------|
| よろしくお願いします | "Please take care of this" | "Thanks in advance!" / "Looking forward to working with you." |
| 〜していただけますか | "Could you please kindly...?" | "Could you...?" (one hedge is enough) |
| お世話になっております | "I am in your care." | (just start with the point) |
| ご確認ください | "Please confirm." | "Could you take a look?" / "Let me know what you think." |
| 〜については | "Regarding about..." | "About..." / "Regarding..." (not both) |
| 少し難しいです | "It's a little bit difficult." | "It's a bit tricky." / "That might be tough." |
| 問題ありません | "No problem will occur." | "That should be fine." / "No worries." |
| ご連絡します | "I will contact." | "I'll reach out." / "I'll send you a note." |

---

## Writing Feedback Output Template

When reviewing user writing, always return this format:

```
📝 WRITING REVIEW

**Original**: [quoted text]

**What works**: [1-2 things done well]

**Suggestions**:
1. [Issue] → [Suggested fix]
   💡 Why: [Explain like a native speaker, not a grammar rule]

2. [Issue] → [Suggested fix]
   💡 Why: [...]

**Native version**:
[Rewritten full text]

**Key takeaway**: [One sentence — the main pattern to remember]
```

---

## Tone Scale (Reference)

```
Very formal ←————————————————————→ Very casual
  "I would be grateful if you       "Hey, could you
   could review this."               take a look?"

Use formal for: external clients, senior execs, formal docs
Use casual for: team Slack, daily standups, PR comments, GitHub issues
```
