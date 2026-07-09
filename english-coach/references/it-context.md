# IT Context Reference — English Coach

> **Goal**: Sound like a native *engineer*, not just a native speaker.
> IT English has its own culture, shortcuts, and unwritten rules.

---

## 1. IT Phrasal Verbs (with image)

### spin up
**Image**: A top or disk starting to *spin* — bringing a new instance/container to life.
- "Can you spin up a new staging environment?"
- "I spun up a local Postgres instance for testing."

### roll back
**Image**: *Rolling* something *backward* on a track — returning to a previous version.
- "We need to roll back the deployment — it's breaking prod."
- "Let's roll back to v1.2 and investigate."

### kick off
**Image**: A *kickoff* in a game — the first action that starts everything moving.
- "I kicked off the migration script an hour ago."
- "Let's kick off the sprint planning meeting."

### scale up / scale out
**Image**: scale up = growing *taller* (bigger machine); scale out = *spreading wider* (more machines).
- "We need to scale up the DB instance before traffic peaks."
- "The service scales out automatically with Kubernetes."

### push back
**Image**: Someone is pushing something toward you and you *push it back* — resisting, delaying, or disagreeing.
- "The client pushed back on the proposed timeline."
- "I'm going to push back on this design — it feels too complex."

### pull in
**Image**: *Reaching out* and bringing something *into* your space.
- "Pull in the latest changes before you start."
- "Can you pull in that library?"

### wire up
**Image**: Connecting *wires* between components — integrating parts together.
- "I still need to wire up the payment service to the checkout flow."
- "Let me wire up the logging middleware."

### rip out
**Image**: *Tearing* something out by force — aggressively removing code/dependencies.
- "We should rip out that legacy module."
- "Let's rip out Redux and switch to Zustand."

### hand off
**Image**: Physically *passing* something from your hands to someone else's.
- "I'll hand off this task to you — here's the context."
- "We'll hand off the project to the client team on Friday."

---

## 2. Tech Idioms

| Idiom | Meaning | Example |
|-------|---------|---------|
| out of the box | works without configuration | "It supports OAuth out of the box." |
| under the hood | internal implementation, hidden from user | "Under the hood, it's using WebSockets." |
| pain point | a frustrating problem area | "The auth flow is a real pain point for users." |
| technical debt | accumulated shortcuts that slow future work | "We need to address the technical debt in the data layer." |
| moving target | requirement that keeps changing | "The spec is a moving target — it changed 3 times this week." |
| low-hanging fruit | easy wins, quick improvements | "Let's fix the low-hanging fruit before tackling the hard stuff." |
| bikeshedding | arguing about unimportant details | "We spent an hour bikeshedding over variable names." |
| yak shaving | doing tedious side tasks before the real task | "I was trying to fix a bug and ended up yak shaving for 3 hours." |
| rubber duck debugging | explaining code to inanimate object to find bugs | "Just rubber duck it — explaining it usually reveals the problem." |
| greenfield | new project with no legacy constraints | "It's a greenfield project — we can choose any stack." |

---

## 3. Slack Communication — Natural Phrases

### Making requests
```
Hey, could you take a look at this when you get a chance?
No rush, but could you review my PR before EOD?
Quick question — do you have 5 mins to chat about X?
```

### Reporting progress
```
Pushed the fix — waiting for CI to confirm.
Still digging into this. I'll update you by [time].
Blocked on X — does anyone have context on this?
```

### Giving updates
```
Just wanted to flag that [issue]. I'll have a fix ready by [time].
FYI: [brief update]. No action needed from you.
Heads up: [important info you should know].
```

### Reacting naturally
```
On it 👍
Makes sense, thanks for the context.
Got it — I'll handle it.
Good catch!
Sounds good to me.
+1 to that
Noted!
```

### Saying you don't know
```
Not sure about this one — let me look into it.
I'd have to double-check, but I think...
That's a good question — I'll get back to you.
```

---

## 4. Code Review Language

### Leaving comments (tone matters)

**Too harsh** → sounds aggressive even if unintentional:
```
This is wrong.
You need to fix this.
This doesn't make sense.
```

**Natural and constructive:**
```
nit: Could we rename this to make it clearer?
I think this might cause issues if X — what do you think?
Could you add a comment explaining why this works?
This looks good! One small suggestion: ...
Have you considered using X here instead?
```

### Comment prefixes (GitHub convention)
| Prefix | Meaning |
|--------|---------|
| `nit:` | Minor style/naming issue, not blocking |
| `LGTM` | "Looks Good To Me" — approving |
| `LGTM with nits` | Approving but has minor suggestions |
| `blocking:` | Must be fixed before merge |
| `optional:` | Take it or leave it |
| `question:` | Just asking, not requesting change |

### Expressing uncertainty (not asserting)
```
I might be wrong, but this could cause a race condition.
Not 100% sure, but I think this should be async.
Worth double-checking: does this handle the null case?
```

### Approval phrases
```
LGTM!
Looks great — nice clean solution.
This is much better than before. Ship it!
```

---

## 5. Standup / Meeting English

### Daily standup format
```
Yesterday I [past tense: what you did].
Today I'm [present continuous: what you're working on].
I'm blocked on [issue] — I need [specific help].
```

**Example:**
```
Yesterday I fixed the auth bug and updated the tests.
Today I'm working on the payment integration.
No blockers.
```

### Raising concerns
```
Before we move on — I have a concern about [X].
Quick note: [Y] might be a problem down the line.
I want to flag that [Z] — should we discuss this?
```

### Proposing ideas
```
One idea: what if we [approach]?
What do you think about [suggestion]?
This might be worth considering: [idea].
Would it make sense to [action]?
```

### Wrapping up
```
I think we're aligned on this.
Let's take this offline.
I'll send a follow-up in Slack.
Should we schedule time to dig into this?
```

---

## 6. Document English (README / Commit / PR)

### Commit message convention (Conventional Commits)
```
feat: add JWT authentication
fix: resolve null pointer in user service
chore: update dependencies
refactor: extract auth logic into separate module
docs: update API documentation
test: add unit tests for payment service
```

**Rules:**
- Use **imperative mood**: "add" not "added" / "adds"
- Keep subject under 72 characters
- Explain *why* in the body if needed, not *what* (the diff shows what)

### README structure
```markdown
# Project Name

One sentence: what it does and for whom.

## Getting Started
## Usage
## Contributing
## License
```

### Code comment conventions
```
// TODO: refactor this once the API is stable
// FIXME: this breaks with unicode input — track issue #123
// HACK: temporary workaround for vendor bug
// NOTE: this is intentionally synchronous
```

---

## 7. Common Abbreviations & Culture

| Abbreviation | Meaning | Context |
|-------------|---------|---------|
| ASAP | As Soon As Possible | Urgency (use carefully — can stress people) |
| EOD | End of Day | Deadline: "by EOD Friday" |
| OOO | Out of Office | Away from work |
| TBD | To Be Determined | Not decided yet |
| TBA | To Be Announced | Decision coming soon |
| FYI | For Your Information | Sharing info, no action needed |
| LGTM | Looks Good To Me | Code review approval |
| WIP | Work In Progress | Not ready yet |
| PR | Pull Request | Code change for review |
| RFC | Request for Comments | Proposal for discussion |
| POC | Proof of Concept | Prototype to test feasibility |
| MVP | Minimum Viable Product | Smallest useful version |
| SLA | Service Level Agreement | Uptime/response commitments |
| RTFM | Read The Fine Manual | (Rude) "It's in the docs." |

### Tone notes
- "ASAP" can feel pressuring — prefer "when you get a chance" or "by [specific time]"
- Always end requests with context: "Could you review this PR? It's blocking the release."
- "Just" as a softener: "Just wanted to check..." (softens) vs. "Just do it." (demands — avoid)
