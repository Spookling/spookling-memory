# Spookling Memory

*Algorithm v2.1 · October 2026*

**Your AI forgets you every session. It brings back the car you sold. It drops your rules the moment the chat gets long.**

Spookling Memory is built to end all three. A word-for-word log that is never edited. Facts with real dates, so an old value closes itself. A timeline written by code, not guessed by the model. **On a three-year test of facts that change, it was right 103 times out of 106 and gave 0 stale answers. Plain search: 37 right, 18 stale.**

**[Get early access](#early-access)** · [See how it works, step by step](https://claude.ai/artifact/MpjJEgJmviijeVTo3S3GhY)

Every number we measured has a result file behind it; every paper and vendor number carries its source. See the [Show-Your-Work Guarantee](#the-show-your-work-guarantee) below.

| The number | What it means | The detail |
| --- | --- | --- |
| **0** stale answers | Old facts stay old | 85 chances to bring back a sold car or an old phone. It took none. Plain search took 18. |
| **103** of 106 | Facts that changed, answered right | Three years of changing jobs, phones, cities, cars. Plain search: 37 of 106. |
| **60** of 61 | Trick questions caught | Questions with no real answer, refused instead of invented. Plain search caught 40. |
| **1/3** the tokens | Tied accuracy, a third of the reading | 13,900 tokens per answer (12,500 recalled plus instructions) against 39,900 for keyword search, tied on the longest-history public test (81.5% vs 82.6%, inside the error bar). |

---

## The problem

**Three ways your assistant lets you down, and what each one costs you.**

### It brings back the past as if it were now

You sold the car in June. In September it plans your road trip in it. You switched phones in May; it still troubleshoots the Pixel. Every stale answer is a decision you have to catch yourself.

> **The fix: dated facts.** "Sold the car" becomes a row that says "car: none, from June". The old row closes. Nothing is deleted, nothing comes back. 0 stale answers in 85 tries.

### It loses your rules when the chat gets long

You said "always use pnpm" and "never touch the migrations folder". The chat got compacted into a summary. The rules went with it. Now you repeat yourself, or you find out after the damage.

> **The fix: the survivor block.** Before the squash, code copies your rules, choices and open items out word for word, then hands them back into every prompt. The model does not have to remember them. They are delivered. (Proven in the paper we built on; our own compaction test is being rebuilt to show it.)

### It guesses dates and counts

"How many times did I see the dentist this year?" "How long since the last deploy?" The model does arithmetic in its head and gets it wrong often enough that you stop trusting any number it gives you.

> **The fix: the code-written timeline.** Code dates every "last Tuesday", builds the timeline, counts, and measures gaps. A date known only to the month comes out as a range, never a made-up day. The model quotes; it does not calculate. (Built; on the public time questions search is still ahead, see straight talk.)

---

## What you get

**Eight pieces, each one built on a published result or our own measurement.**

Every piece exists because a paper or a measurement of ours said it had to. Pseudocode, papers and worked examples are on the [step-by-step page](https://claude.ai/artifact/MpjJEgJmviijeVTo3S3GhY).

### 01. The Never-Edited Log

Every message, word for word, with its time. Nothing is summarised, nothing is rewritten. Every other store is rebuilt from it.

**Why it matters:** a wrong summary is wrong forever. A log can always be re-read.

### 02. The Letter-for-Letter Tagger

A cheap model points at facts and events in each message using your exact words. Code checks the quoted words are really there before anything is stored.

**Why it matters:** a model that paraphrases can invent a value. A model that only points cannot.

### 03. Facts That Close Themselves

Rows like "phone = Pixel 8, true from 3 Mar up to 20 May 2025". A new value closes the old one. "What was true in December" is a lookup.

**Why it matters:** this is where the 103 of 106 and the 0 stale answers come from.

### 04. Cue-Word Events

Things that happened once, with when it happened kept apart from when it was said, and a class word stored beside your word: "the gig" is filed under "concert".

**Why it matters:** a strong model fell from 87% to 62% at 8,000 tokens when the question and the answer shared no words (NoLiMa). The cue makes it a literal match.

### 05. The Two-Hop Finder

Keyword search, meaning search, and a walk along links: my sister, then Rachel, then the places she was mentioned with. A reranker keeps the best eight snippets.

**Why it matters:** 38 of 40 right on "the place where I saw my sister" questions. Multi-hop on LoCoMo: 30 of 41, search 19.

### 06. The Code-Written Timeline

Code writes the dates part of the prompt: events in order, counts, gaps, "how long since". Month-exact dates give a range of days.

**Why it matters:** models lose 4 to 12 points when they do date math themselves (STAIR, TISER). Now they do not have to.

### 07. The Survivor Block

Up to forty lines of rules, choices, failed tries and open items, copied out before compaction and put back on top of every prompt after.

**Why it matters:** in the paper we built on, a summary kept 0 of 10 planted facts, and a re-injected block arrived 138 times out of 138.

### 08. The No-Guessing Rule

The model must first say, true or false, whether what you are assuming is actually on record. It is never offered "I don't know" as an easy exit. Code decides what happens to a refusal.

**Why it matters:** 30 of 30 unanswerable questions refused on LongMemEval; 30 wrong answers against search's 40.

---

## How it works

Nine boxes. Two of them are records that only grow (the log and the judgement log). The other seven are views, rebuilt from those two.

1. **Log:** every message, word for word, never changed
2. **Facts:** things true for a while, with a start and end date
3. **Events:** things that happened once, day it happened + day it was said
4. **Rules:** your standing orders, pinned on top
5. **Names:** "my sister" = Rachel, "gig" = concert
6. **Judgement log:** every call a model made, written before it changes anything
7. **Profile page:** 200 lines of what is true about you right now
8. **Survivor block:** 40 lines that come back after a chat is squashed
9. **Search index:** keyword search + meaning (semantic) search

The algorithm, trimmed to the bones:

```text
# 1. SAVE (when a chat ends)
FOR each message: APPEND to LOG
tags <- SMALL_MODEL.tag(user messages)          # exact words + labels
FOR each tag:
  IF words not in message letter-for-letter: DROP, record it
  date <- DATE_RULES(time words)                # code, never a model
  IF event: status <- happened / planned / negated
  ELSE IF flag word or odd date or unsure: SLOW_PATH
  ELSE IF first value or same value:       FACTS now
  ELSE IF change word ("sold", "new"):     close old row, open new
  ELSE:                                    SLOW_PATH   # a surprise

# 2. SORT OUT (before the next question, if anything new was saved)
FOR each item in SLOW_PATH:
  ruling <- BIG_MODEL.rule(message, proposal, history); check its words; record it
FACTS  <- REPLAY(JUDGEMENT_LOG)
IF a key has two values on one day: STOP, keep last good, tell a human
EVENTS <- BUILD(LOG)
PROFILE_PAGE, SURVIVOR, INDEX <- rebuild

# 3. ANSWER
when   <- DATE_RULES(question) OR now
facts  <- FACTS true on when;  events <- EVENTS matching
hits   <- RERANK(KEYWORD_SEARCH + MEANING_SEARCH + LINK_WALK(names)), top 8
prompt <- [SURVIVOR, PROFILE_PAGE, facts, events, hits, TIMELINE(events), question]
reply  <- READER(prompt)       # must write Premise T/F, Values T/F, "Answer:"

# 4. DECIDE (code, not the model)
IF NOT refusal:                               RETURN answer
IF Premise=false AND search was strong:       re-ask once with the best lines
IF Values=false AND time block has a number:  re-ask once: "derive it from your quotes"
ELSE:                                         RETURN "I don't know" + what is missing

# 5. WHEN THE HARNESS SQUASHES A CHAT
KEEPERS  <- each rule / choice / failed try / open item / value / path, word for word
SURVIVOR <- RULES first, then newest KEEPERS, max 40 lines
PUT SURVIVOR on top of the next prompt
```

| Who | Does what | Runs on |
| --- | --- | --- |
| Small model | points at words, adds labels | Haiku 4.5, cloud |
| Big model | rules on one flagged item | Sonnet 5, cloud |
| Reader | answers from the lines we hand it | the harness's model |
| Scorers | meaning search + rerank | two tiny local models, optional |
| Code | every date, count, check, decision | your machine |

**No model ever** computes a date, counts, writes to Facts, or turns an answer into a refusal. The small model points, the big model rules on surprises, the reader quotes. Code does the rest.

---

## The receipts

**Measured, not claimed. Same questions, same reader, both arms.**

We ran Spookling Memory and the standard cheap baseline (keyword search over the same log) on the same questions with the same answering model, Claude Sonnet 5, and one strict judge that counts a wrong detail as wrong. Pass marks were written down before the runs. Percentages are over the answerable questions.

| Test | Spookling | Search | Result |
| --- | --- | --- | --- |
| Facts that change | **103 of 106** | 37 of 106 | +62 points |
| LoCoMo, public, 239 answerable of 300 | **82.0%** | 77.8% | ahead by 4.2 points |
| LongMemEval, public, 470 answerable of 500 | **81.5%** | 82.6% | tie on score, 1/3 the tokens |
| Rules after compaction | **42 of 44** | 40 of 44 | 8 of 10, search 6 |

### Facts that change

Three years of generated chats where jobs, phones, cities and cars change on known dates. **Stale answers 0 vs 18.** Interval on the lead: +52.8 to +70.8. The widest gap on this page, and the one that matters most day to day.

### LoCoMo

300 questions sampled from the public set. **Trick questions caught: 60 of 61, search 40. Multi-hop: 30 of 41, search 19.** Interval −0.8 to +9.2, so the lead is not yet beyond doubt.

### LongMemEval

Almost the same score: the 1.1-point gap is inside the error bar (−4.7 to +2.8), so neither side can be called better. Ours reads **13,900 tokens per answer against 39,900**. Fewer wrong answers: 30 vs 40. All 30 unanswerable questions refused, search 24. Counting correct refusals over all 500: 413 vs 412. Ahead on newest-value and cross-chat questions.

### Rules after compaction

Every miss in both arms is on the rule questions. Our own suite's summary turned out to keep rules well on its own, so the next version of this test uses a real Claude Code compaction. The design rests on a published 138-of-138 result.

---

## Versus the field

**What only Spookling Memory does.**

The other systems are good at what they do. None of them, as far as they say, does all of this. Vendor scores are theirs, self-reported with their own readers and the benchmarks' standard yes/no judges; ours use one strict judge and a matched control arm.

| System | Log never edited | Facts with from / to dates | Dates by code, not the model | Refuses trick questions (measured) | Rules re-injected after compaction | Pre-registered pass marks, matched control arm, strict judge | Their headline |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Spookling Memory** | ✓ | ✓ from, up to, said on, recorded | ✓ | ✓ 60 of 61, 30 of 30 | ✓ by hook, Claude Code | ✓ | 103 of 106 on changing facts; tied with the best baseline at 1/3 the tokens |
| Plain keyword search | ✓ | ✗ | ✗ | ~ 40 of 61, 24 of 30 | ✗ | n/a our control arm | 82.6% LongMemEval in our run |
| A memory file the model rewrites (our own v1, the same approach as Claude Code's memory tool) | ✗ rewritten on every save | ✗ | ✗ | ? | ~ always loaded; the model decides what stays | n/a | 66 to 78 of 106 on changing facts in our run; over its size cap on 140 of 199 chats |
| Mem0 | ✗ extracted strings | ✗ | ✗ | ? | n/a | ? | 94.4% LongMemEval, 92.5% LoCoMo, self-reported; 72.7% on a LongMemEval subset in an independent study (MemDelta) |
| Zep / Graphiti | ~ raw episodes kept, plus an extracted graph | ✓ set by the LLM | ✗ | ? | n/a | ? | 94.8% on DMR (its paper); third-party LoCoMo quotes range from 75.1% to 94.7% |
| Mastra Observational Memory | ? the notes replace the context; raw storage not stated | ~ dated notes | ✗ | ? | ~ the notes are the context | ? | 94.9% LongMemEval with gpt-5-mini, 84.2% with gpt-4o, self-reported |
| Agent Zero Memory (paper) | ? | ~ timestamped events | ? | ? | ? | ? | 95.6% LongMemEval, 93.6% LoCoMo, self-reported |

✓ yes · ✗ no · ~ partly · ? not stated by them, or not seen by us · n/a does not apply. Sources at the [bottom](#sources).

---

## The Show-Your-Work Guarantee

**Every number we measured has a file behind it. If one does not reproduce, we print the correction at the top of this page.**

Result files, judge logs, both judges' verdicts, the pass marks written before the runs, and the misses we traced. The harness ships with the package, so you can run it on your own memory setup. We publish our losses next to our wins; they are in the "straight talk" box below.

### Bonuses

- **Bonus 1: The benchmark harness.** Two public suites run and a third built, plus three of our own, with a strict judge, bootstrap intervals and paired comparisons. Point it at any memory system.
- **Bonus 2: The step-by-step algorithm.** Every step as pseudocode, who does what, two worked examples, and the fifteen sources (papers and books) that shaped it. Built to be rebuilt by anyone.
- **Bonus 3: The research library.** Twenty-seven papers and ten books on memory, retrieval and time, read end to end, with notes on what each one gave the design.
- **Bonus 4: The pass marks.** Ten marks written before the v2 runs, with pass, fail or still open next to each. You will know what we aimed at before you see what we hit.

---

## Early access

**Claude Code first. One folder in your repo. Your data stays yours.**

A `.memory/` folder, one plugin, the tagger and reader models you already pay for. Early access opens with the npm package; Claude Code users go first, Codex and the Spookling app follow. Until then, run it from source.

```bash
npx @spookling/memory init     # the published plugin, coming with early access
git clone <memory-bench>       # today: run the memory server from source, see docs/operations.md
```

---

## Install

*The wizard is coming with early access. Until then, run from source as above.*

One command. One wizard. No config files to hand-edit.

```bash
npx @spookling/memory init
```

It opens with the Spookling wordmark in block letters and the ghost flying across your terminal. Then it gets to work:

1. **Checks your machine.** Node, plus uv or Python.
2. **Asks which harness you use.** Claude Code first. Codex follows.
3. **Asks for your keys.** Anthropic for the models. OpenRouter too, if you want the tagger routed there.
4. **Installs the local memory server.** It runs on your machine. Your harness talks to it over HTTP and MCP.
5. **Writes the Claude Code wiring.** Three hooks that call the server: `Stop` saves the chat when it ends, `UserPromptSubmit` hands the right lines to each prompt, `PreCompact` copies the survivor block out before the squash. Plus the MCP config, so Claude Code can see the memory tools.
6. **Runs a one-line test.** You see memory answer before you leave the wizard.
7. **Shows a done screen.** That is it.

What it writes, in plain terms:

| Where | What |
| --- | --- |
| Claude Code hooks | `PreCompact`, `UserPromptSubmit`, `Stop` |
| Claude Code MCP config | the Spookling Memory server and its tools, including `memory_reset` |
| Your machine | the local memory server |
| Your repo | the `.memory/` folder |

## Paste this into Claude Code

*The block lands with the wizard, coming with early access.*

One block you paste into Claude Code (or Codex). It will tell your assistant to run the wizard in the foreground, so the ghost plays in your terminal, then confirm the hooks are live and say "Spookling Memory is in". It will also ship as `PASTE-ME.md` in this repo.

---

## Your memory moves with you

*Coming with early access.*

Your memory is not locked inside a tool. It is a folder.

`.memory/` holds two records. Both are append-only. Nothing in them is ever rewritten.

- **The log.** Every message, word for word, with its time.
- **The judgement log.** Every call a model made, written before it changes anything.

Everything else is a view: facts, events, rules, names, the profile page, the survivor block, the search index. Views are rebuilt from the two records. Facts are rebuilt by replaying the judgement log, so past calls are reused, not re-guessed. A view is never the only copy of anything.

So moving is one file:

```bash
spookling-memory export                        # one .spookling archive of the two records
spookling-memory import <archive>.spookling    # restore it anywhere; the views rebuild
```

Start in Claude Code. Carry the same memory to Codex or the Spookling app when they land. Same facts, same rules, same history. No re-teaching. No vendor holding your past hostage.

---

## Reset without regret

*Coming with early access.*

New job. New project. A month you would rather your assistant stopped bringing up. One click makes everything before now irrelevant.

Reset writes one "reset" ruling to the judgement log at time T. From then on:

- **Every open fact closes at T.** It stops being true now. It is still true for the dates it covered.
- **Every event before T is marked "before reset".** It shows up only when you ask about a date before T.
- **Rules and survivor lines from before T are unpinned.** They stop riding on every prompt. They are kept.
- **Nothing is deleted.** Reset is one more line in a record that only grows.

Changed your mind? Undo appends the reverse ruling. Everything comes back as it was. The ghost was only resting.

For builders:

```text
POST /v1/reset         # one "reset" ruling at now
POST /v1/reset/undo    # the reverse ruling; everything returns
```

The same reset is a `memory_reset` MCP tool in Claude Code. The reset button in the Spookling app calls `POST /v1/reset`. This repo ships the endpoint; the button lives in the app.

Reset is not delete. If you need data gone for good, that is the purge command, which is planned (see Privacy).

---

## Straight talk

- **Not published yet.** The package and the Claude Code wiring of the no-guessing rule are being finished. The numbers come from our benchmark runner, build 6.0, re-scored with the v2.1 rule (the "derive it" re-asks re-run, the look-again re-asks pending).
- **Where search still beats us:** time questions on LongMemEval (92 vs 106 of 127; the fix is built and in the pending run) and fewer refusals (42 to our 57). Our weakest spot is two-hop by a person's other name, 12 of 35.
- **Tagging costs money up front.** On a one-question-per-history benchmark that made our run cost 2.2x search; where thirty questions share a history, 1.06x. Real use reads each memory many times.
- **The leaders self-report 94 to 96%** with the benchmarks' yes/no judges and their own readers. We score with a strict judge; we will publish a lenient-judge pass beside it. Our fixes were made after seeing the misses on every suite here; a held-out split is next.

---

## Method, pass marks and cost

For the people who want the file names.

- Reader Claude Sonnet 5 in both arms; strict three-way judge on Sonnet 5; second judge Haiku 4.5 (agreement 0.85 to 0.94; 0.56 on the two-hop suite). Intervals from 1,000 bootstrap resamples.
- Updates: 120 questions (106 answerable), our generated suite. LongMemEval: all 500 (470 answerable). LoCoMo: 300 of 1,986 sampled with seed 7 (239 answerable, 61 traps); its answer key is about 6.4% wrong (Penfield audit, April 2026). Compaction: 50 (44 answerable). Two-hop: 150, no search control yet.
- Floors with no memory, Haiku reader: LongMemEval 1 of 470, LoCoMo 2 of 1,540, updates 0 of 106, compaction 41 of 44. Ceilings with the whole history: updates 81 of 106 with Haiku, 106 of 106 with Opus.
- Pass marks (13 October 2026): stale under 5% pass (0); retired facts as gone pass (9 of 9); unanswerable refused 90%+ pass on every suite; early facts within 10 of late pass; instructions 90%+ fail (8 of 10); store under 25% of log pass (22%); reader tokens under 25% of everything pass (12%); judge agreement pass except two-hop; human audit open; the pre-registered target (beat the leaders with the lower interval bound) not met.
- Run cost, list price, search first: updates $0.65 / $2.73 (4.2x); compaction $1.17 / $1.75 (1.5x); LoCoMo $9.94 / $10.55 (1.06x); LongMemEval $42.19 / $94.60 (2.2x). Our runs include tagging every history once (about 105,000 tagger tokens per 115k-token history).
- The numbers come from benchmark build 6.0 (algorithm v2 stores) re-scored with the v2.1 decision rule; a fresh v2.1 run is 108 of 500 in.

### Privacy

Each message goes to the tagger's API once; recalled lines go to the reader's API per question; a flagged tag (a contradiction, a rule, a correction, a doubtful date, a low-confidence tag, an unlisted class) goes to a stronger model once, with its message. The log is append-only; a purge command is planned. Keep `.memory/` out of shared repos until then.

---

## Sources

Spookling Memory is built by Spookling, a personal AI assistant company: an AI assistant that works for you while you sleep. Benchmarks, pass marks and every result file are in `memory-bench/`; the algorithm is on the [step-by-step page](https://claude.ai/artifact/MpjJEgJmviijeVTo3S3GhY).

Vendor numbers, checked 18 October 2026: Agent Zero Memory (arXiv 2608.29606), Mastra (mastra.ai/research/observational-memory), Mem0 (mem0.ai/blog/ai-memory-benchmarks; MemDelta, June 2026), Zep (arXiv 2501.13956 and third-party quotes), Penfield, "We audited LoCoMo" (April 2026).

Papers named above: NoLiMa 2025; STAIR and TISER 2025 to 2026; Delivery, Not Storage 2026; Kadavath et al. 2022.
