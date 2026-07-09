# Grammar Reference — English Coach

> **Principle**: Don't memorize rules. Build *mental scenes* for how grammar feels.
> English grammar works by *spatial and temporal images*, not by verb conjugation charts.

---

## 1. Contractions — When to Use (and When NOT to)

**Always use contractions in:**
- Casual Slack, chat, spoken English
- Code comments, PR descriptions
- Daily standup

**Avoid contractions in:**
- Formal documentation (technical specs, legal docs)
- Formal email to external clients/executives

### Most important contractions

| Full form | Contracted | Situation |
|-----------|------------|-----------|
| I am | I'm | Always in speech/chat |
| it is / it has | it's | Always (watch: "its" = possessive) |
| I would / I had | I'd | Common in speech |
| I will | I'll | Common in speech |
| do not | don't | Everywhere except formal |
| did not | didn't | Everywhere except formal |
| cannot | can't | Everywhere except formal |
| I have | I've | Common in speech |
| should have | should've | Spoken; NOT "should of" |
| would not | wouldn't | Common |

**Unnatural without contraction:**
- "I am not sure" → sounds formal/stiff → "I'm not sure" ✅
- "That is a good idea" → "That's a good idea" ✅

---

## 2. Tense — By Scene, Not by Rule

### Present Perfect vs Simple Past

**Mental image for Present Perfect** (have + past participle):
> A bridge connecting the past TO the present moment. The past action still *matters now*.

**Mental image for Simple Past:**
> A closed box. The action is done, sealed, has no connection to now.

| Scene | Tense | Example |
|-------|-------|---------|
| "The bug was fixed and it matters now" | Present Perfect | "I've fixed the bug." |
| "The bug was fixed, specific time in the past" | Simple Past | "I fixed it yesterday." |
| "Have you ever...?" (life experience, no specific time) | Present Perfect | "Have you ever used Kubernetes?" |
| "Did you...?" (specific occasion in the past) | Simple Past | "Did you deploy this morning?" |
| Announcement (news feel, affects now) | Present Perfect | "We've released v2.0." |

**Common mistake**: "I have gone to Tokyo last year." → Wrong. Specific time → Simple Past: "I went to Tokyo last year."

---

### Present Continuous — The "In-Progress" Movie

> Imagine a camera recording RIGHT NOW. You can *see* the action happening.

- "I'm working on the auth feature." (right now, ongoing)
- "We're migrating the database this week." (temporary ongoing process)
- "She's joining the team next month." (planned near future — very natural!)

---

### Will vs Going to — Commitment vs Plan

| Feel | Form | Example |
|------|------|---------|
| Decision made *right now*, spontaneous | will | "It's crashed — I'll restart it." |
| Plan already made before speaking | going to | "I'm going to refactor this after lunch." |
| Prediction with evidence (you can see it) | going to | "Look at this code — it's going to fail." |
| General future prediction | will | "AI will change how we code." |

---

## 3. Articles — a / an / the / ∅

### The Core Image

**"a / an"** = introducing a new character to the story. The listener doesn't know which one yet.
> "I found **a** bug." (One bug, any bug, you're introducing it)

**"the"** = pointing at something *already known* (by context, by shared knowledge, because it's unique).
> "I fixed **the** bug." (The one we just talked about)
> "**The** server is down." (The one we all know about)
> "**The** sun rises in the east." (Unique, only one)

**"∅" (no article)** = uncountable things, plurals in general, abstract concepts.
> "I write **code** every day." (code in general)
> "**Bugs** are inevitable." (bugs in general, not specific ones)

### Common Japanese Speaker Mistakes

| Mistake | Correct | Why |
|---------|---------|-----|
| "I'm developer." | "I'm **a** developer." | Singular countable noun → needs "a" |
| "I pushed code to the main branch." | ✅ Correct | "the main branch" is specific |
| "Can you explain me the algorithm?" | "Can you explain **the** algorithm to me?" | "explain to someone" (not explain someone) |
| "I have good news." | ✅ Correct | "news" is uncountable → no article |

---

## 4. Prepositions — Direction and Relationship Images

> Don't memorize prepositions with words. Memorize the *image* of the relationship.

### Key images

**ON** = touching a surface / contact
- "working **on** a feature" (your attention is *on* it, like a surface)
- "depends **on**" (resting on a foundation)
- "focusing **on**"

**IN** = inside a container / enclosed
- "**in** the meeting" (you're inside that context)
- "**in** 5 minutes" (inside a time boundary)
- "believe **in**"

**AT** = exact point / location
- "**at** the office" (precise location)
- "good **at** coding" (precise skill point)
- "looking **at**"

**FOR** = purpose / duration / recipient
- "**for** the user" (purpose)
- "**for** 3 hours" (duration)
- "waiting **for**" (recipient/target)

**WITH** = accompaniment / tool
- "working **with** React" (using it as a tool)
- "dealing **with** a bug" (together with the problem)

### Common IT collocations

| Phrase | Preposition | Image |
|--------|-------------|-------|
| work on | on | surface/focus |
| deal with | with | together |
| depend on | on | resting/relying |
| log in to | in + to | enter a container |
| connect to | to | direction toward |
| integrate with | with | tool/accompaniment |
| responsible for | for | purpose/ownership |
| familiar with | with | close accompaniment |

---

## 5. Modal Verbs — Pressure and Distance

> Think of modals as a *pressure dial* (how strong?) and a *distance dial* (how polite?).

```
CERTAINTY:  must > should > would > could > might > may
POLITENESS: can < could < would < might (more distance = more polite)
```

| Modal | Feel | Example |
|-------|------|---------|
| must | Strong obligation/certainty (internal) | "This **must** be a cache issue." |
| have to | Strong obligation (external rule) | "We **have to** ship by Friday." |
| should | Recommendation/expectation | "You **should** add tests." |
| would | Conditional / polite / past habit | "**Would** you mind taking a look?" |
| could | Ability / weak possibility / polite ask | "This **could** be a race condition." |
| might | Weak possibility | "It **might** work if we..." |
| can | Ability / permission (direct) | "**Can** you review this?" |

**Code review tone:**
- "You need to fix this." → ⚠️ too blunt
- "You should fix this." → direct but OK
- "Could you address this?" → ✅ polite
- "You might want to consider..." → ✅ very soft

---

## 6. Questions and Requests — Direct vs Indirect

### Directness scale

```
Direct (less polite) ←—————————————————→ Indirect (more polite)
"Fix this."    "Can you fix this?"    "Could you fix this?"    "I was wondering if you could take a look?"
```

**In most English work environments (especially async/remote), default to "Could you...?"**

### Tag questions (casual confirmation)
- "That's the right approach, **isn't it?**"
- "You're free after lunch, **aren't you?**"
- "This won't break anything, **will it?**"

### Softeners before requests
- "Just a quick question — ..."
- "When you get a chance, could you...?"
- "No rush, but..."
- "I might be wrong, but..."
