# CodeAgentMonitor (CAM)

**English** · [Italiano](README.md)

Time, cost and message counts for your **Claude Code** and **GitHub Copilot** conversations,
read from the files those tools already write on your own machine. With a desktop UI, a live
terminal dashboard, and a segment for the Claude Code statusline.

For a team there is also a [telemetry collector](#more-machines-the-team-panel) that brings
several machines together, with three privacy levels to choose from.

Standard library only: **nothing to install**, no Python packages and no npm.

![MIT licence](https://img.shields.io/badge/licence-MIT-blue) ![stdlib only](https://img.shields.io/badge/dependencies-none-brightgreen)

![CodeAgentMonitor](docs/CodeAgentMonitor.png)

*Project names and conversation titles are blurred in the screenshots. The numbers are real.*

> **Note on language.** The source code, the comments and the user interface are in Italian —
> that is deliberate, and the [Italian README](README.md) is the fuller reference: it carries
> longer design rationale for several decisions. This page covers everything you need to
> install, run and understand the tool. Making the application itself bilingual is
> [on the backlog](TODO.md).

---

## What it is for

Claude Code does not tell you how much you are consuming, or where. This tool reads the
transcripts it already writes and answers three questions that are easy to confuse:

- **how much have I consumed** — tokens, messages, actual working time, and how much of your
  plan limits you have burned through;
- **what would it cost at API list price** — the value of that consumption, which on a
  subscription **you never pay**;
- **how much did I actually spend** — the monthly fee, or the metered charge if you use the API.

```
MONTH      SESS  PROJ     TOKEN  OUTPUT   IF IT WERE API    PAID  RETURN
2026-03 *     9     4  1842.20M   5.10M         $1,204.6  $20.00   60.2×
2026-02       6     3   980.44M   2.31M           $612.8  $20.00   30.6×
2026-01       1     1     4.02M     71k             $4.10  $20.00    0.2×
TOTAL                  2826.66M   7.48M         $1,821.5  $60.00   30.4×
```

January at `0.2×` means that month's fee was paid almost for nothing.

---

## Installation

You need **Python 3.9+** and Claude Code. Nothing else.

```bat
git clone https://github.com/mlosavio/CodeAgentMonitor.git
cd CodeAgentMonitor
copy config.example.json config.json
python cam.py
```

On **Debian and Ubuntu** the desktop UI needs one extra package: there, `tkinter` does not ship
with Python. The CLI works without it.

```sh
sudo apt install python3-tk        # only for python cam_gui.py
```

Open `config.json` (or the **Configura** button in the UI) and set your plan and what you pay
per month: it is the one piece of information the tool cannot work out on its own.

For the Claude Code statusline segment:

```bat
python install_statusline.py           :: install
python install_statusline.py --wrap    :: keep the statusline you already have
```

---

## Usage

```bat
python cam_gui.py           :: desktop UI
python cam.py               :: summary of recent sessions
python cam.py --by-month    :: consumption, spend and return per month
python cam.py --by-project  :: totals per project
python cam.py --traces      :: one turn per row, not one session
python cam.py --trend       :: trends over time, with indicators
python cam.py --watch       :: live dashboard in the terminal
python cam.py --json        :: machine-readable output
```

| Option | Effect |
|---|---|
| `--base PATH` | transcript folder (default `%USERPROFILE%\.claude\projects`) |
| `--config PATH` | alternative configuration file |
| `--billing subscription\|api` | how usage is paid for; overrides the configuration |
| `--project NAME` | filter by substring of the path or project name |
| `--since` | `7d`, `24h`, `90m`, `oggi` (today), `2026-03-01` |
| `--top N` | maximum sessions (or turns with `--session`); `0` = all |
| `--session UUID` | detail for one session, prefix is enough |
| `--traces` | [one turn per row](#turns) instead of one session; with `--json`, includes them |
| `--search TEXT` | with `--traces`: searches prompts, tool names and projects — and [inside answers](#searching-and-seeing-where) if the text is archived |
| `--export-turni FILE.jsonl` | [the selected turns as a dataset](#exporting-turns-as-a-dataset) |
| `--trend` | [trends and indicators](#trends-and-indicators) |
| `--grana giorno\|settimana\|mese` | with `--trend`: bucket width (day / week / month) |
| `--finestra DAYS` | with `--trend`: days compared against the DAYS before |
| `--chat` | with `--session`: the conversation as Markdown |
| `--watch` / `--interval S` | live view of the most recently modified session |
| `--idle-gap S` | pause beyond which time no longer counts as "active" (default 300s) |
| `--no-cache` / `--clear-cache` | ignore / empty the analysis cache ([the archive stays](#the-archive-cam-localdb)) |
| `--dimentica-testo` | delete conversation text from the archive; the numbers stay |
| `--archivio` | [how big the archive is, and what makes it big](#how-big-it-is-and-how-to-shrink-it) |
| `--no-color` | no ANSI (also honours `NO_COLOR`) |

[GitHub Copilot](#github-copilot) sessions appear alongside Claude Code ones, with cost and
tokens shown as "—".

---

## Desktop UI

```bat
python  cam_gui.py     :: with a console, so you can see tracebacks
pythonw cam_gui.py     :: without a console
```

Same logic as the CLI — it imports it as a module — so the numbers match to the digit.

- **Tiles** across the top: what you have paid, what it would be worth at list price, active
  time, messages, the session in progress, and **how much of your plan limits you have used**.
- Six tabs: **Progetti** (projects), **Sessioni** (sessions, with the title Claude Code assigns
  to the conversation), **[Traces](#turns)**, **[Andamento](#trends-and-indicators)** (trends),
  **Mesi** (months) and **Persone** (people). Columns sort on the underlying numbers, not the
  formatted strings, so `$2,055.7` does not end up below `$301.4`.
- **Persone** shows consumption across several machines, and is only populated once you have
  started the [collector](#more-machines-the-team-panel). The first column header reads
  *Persona*, *Postazione* or *Insieme* depending on the privacy level in force, so you can tell
  without asking whether you are looking at people or at codes. **Double-click a seat** to see
  what it worked on, project by project. If you have imported the billing export, next to
  *Hai pagato* (what you paid, a model estimate) you also get *Fatturato* — what the console
  actually charges — and **seats that pay without consuming appear too**, which no other row
  would show.
- **Every column header explains itself** on hover.
- **Double-click a session** to reopen the conversation, with Markdown export.
- **Double-click a turn** in the Traces tab to see its spans: where the time went.
- The **search box** looks in project names, titles, **your prompt text and the names of the
  tools used**. A session stays visible even when the text you searched for is in only one of
  its turns; with text archiving on, each turn shows
  [the fragment of conversation the word appears in](#searching-and-seeing-where).
- **In the Persone tab**, the *⚠ n da controllare* chip isolates seats where billing and
  consumption [do not line up](#the-third-source-the-billing-export), and above the table sits
  [month-by-month adoption](#adoption-month-by-month).
- **Live**: the rightmost tile follows the active session and refreshes every 2s, reading only
  the new bytes of the transcript. It turns green while that session is working.
- **Auto-refresh every 5 minutes**, configurable.
- Period and project filters, JSON export and [JSONL turn export](#exporting-turns-as-a-dataset),
  `Ctrl+C` copies the row, F5 refreshes.

Look and feel: flat surfaces, the table drawn on a Canvas, light/dark theme following the system
(title bar included), forced with `--light` / `--dark`.

Options: `--theme auto|light|dark`, `--tab progetti|sessioni|traces|andamento|mesi|persone`,
`--live`, `--detail <uuid>`, `--auto-refresh MIN`, `--locale us|it`.

The **Configura** button opens real settings, not the raw JSON: subscription, team, appearance,
statusline and price list. If a change needs a restart, the application restarts itself.

---

## Rereading conversations

**Double-click a project** in the Progetti tab to drill down to its conversations. The
**Sessioni** tab is the index of your history: title, date, project, duration, cost.
Double-click a row for:

- **Conversazione** — what you asked and what was done, in order, with tool calls summarised on
  one line (`⚙ Read, Bash, Edit`) instead of reproduced in full;
- **Costi** — the same walkthrough, with tokens and cost per turn;
- **Esporta .md** — the conversation as Markdown, with title, date, duration and costs at the top.

### Exporting a whole project

The **Esporta ▾** menu has two entries: *Dati in JSON* (the numbers, to process elsewhere) and
**Conversazioni in Markdown**, which exports **everything you are currently looking at** — so
filter by project or period first, then export. You get one folder per project plus an index:

```
esportazione/
  indice.md
  MyProject/
    2026-03-14 Rework the dashboard layout [a1b2c3d4].md
    2026-03-02 Add CSV export [e5f6a7b8].md
```

`indice.md` groups by project and lists date, title (linked to the file), messages, active time
and list-price value. Filenames start with the date, so alphabetical order is already
chronological.

From the command line, with the same filters:

```bat
python cam.py --session a1b2c3d4 --chat > conversation.md
python cam.py --project MyProject --export-md .\export
python cam.py --since 30d --export-md .\export --with-subagents
```

Subagent messages are excluded by default: they are internal work and would break the thread of
the discussion. The window shows at most the last 400 messages; the export contains them all.

Exporting rereads each session in full — the text is not in the cache, which holds only numbers
— so it takes a few minutes across many conversations. In the UI it runs on a separate thread
with progress in the status bar, and asks for confirmation above 40 conversations.

### Where conversations are stored

**This tool does not save your conversations**: Claude Code already writes them, one JSONL file
per session, and they are read here strictly read-only.

```
%USERPROFILE%\.claude\projects\<project>\<uuid>.jsonl
```

**Transcripts do expire, though.** Claude Code has `cleanupPeriodDays`, which deletes those
older than N days. To keep them longer, in `~/.claude/settings.json`:

```json
"cleanupPeriodDays": 3650
```

The price is disk space, which grows by a few hundred MB per month of heavy use. For the
conversations you actually care about, Markdown export remains the sturdier answer: it is your
file, readable without this tool and without Claude Code.

### The archive: `cam-local.db`

The **numbers** are kept: in a SQLite file next to the script. The archive has two halves with
opposite rules, and that is the whole design:

| table | what it is | can it be thrown away? |
|---|---|---|
| `file` | the record derived from each transcript, with size and date | **yes**: it can be reread |
| `sessione`, `turno` | what was measured | **not always** — see below |

Every session says where it came from:

- `origine = 'derivato'` — all its transcripts still exist, the row can be rebuilt;
- `origine = 'acquisito'` — at least one is missing (`file_mancanti` says how many), or the
  source never had a transcript at all.

From that distinction follows the rule that holds up everything else: **only what is derived
gets deleted and rebuilt**. A parser format change empties the `file` table and does not touch
the archive; `--clear-cache` does the same by hand. The cost of a future migration therefore
stays proportional to the data that no reread could reproduce.

### Sessions that outlive their transcript

When `cleanupPeriodDays` deletes a transcript, its row in `file` disappears but **the session
and its turns remain**, marked as acquired — and they keep appearing in the totals, flagged with
**▪**. They are the only memory left of that work: without them, the cost of a past month would
quietly shrink over time.

| | still there? |
|---|---|
| session and turn numbers | **yes**, all of them |
| conversation text | **yes**, if `archivio.testo` was on |
| span waterfall | **no**: it lives in the detail, which is not archived |
| tool results | **no**, never archived |

If instead **only one** of a session's files disappears — typically a subagent's — the session
keeps being scanned and its numbers **go down**, because that work can no longer be read and is
not invented. `file_mancanti` says how many files are involved, so the difference stays
explainable instead of being a mysterious drop.

### Archiving the text too

By default the archive holds **numbers only**, plus the first 200 characters of your prompts so
a turn can be recognised in a list. Conversation content is archived only if you ask for it —
*Configura → Archivio*, or in `config.json`:

```json
"archivio": { "testo": true }
```

What goes in is **your questions and the answers**, not tool results: in a real session the text
is 1.6% of the transcript and everything else is file contents and command output, already on
disk and reread by nobody. Across 227 MB of transcripts that is about 4 MB.

What changes when it is on:

- **search reaches inside the answers**, with a full-text index (SQLite FTS5, falling back to
  `LIKE` if that SQLite build lacks it); three letters is enough to search;
- **a conversation stays readable after its transcript is gone**: the only way not to lose it
  when `cleanupPeriodDays` fires.

Turning it on causes every transcript to be reread once. Turning it off deletes nothing: to
delete, run `python cam.py --dimentica-testo`, which removes the text and keeps the numbers.

Keeping the content of your own conversations on disk is a decision, not a default to discover
afterwards — which is why it is off, and why there is an explicit way to undo it.

The archive is not there to make the monitor fast: across 227 MB of transcripts a full reread
costs under two seconds, because the files have few very long lines. It is there to **query**
what was measured:

```sql
SELECT substr(session_id,1,8), round(costo,2), richieste, tool, substr(prompt,1,40)
FROM turno ORDER BY costo DESC LIMIT 10;
```

and to **keep** it once the transcript is gone. `*.db` is already in `.gitignore`.

### How big it is, and how to shrink it

```
python cam.py --archivio
```

tells you how much space the file takes and **what takes it** — without that answer the only
possible moves are delete everything or touch nothing:

```
      1.1 MB  in total
    452.4 KB  turns                  40%
    371.2 KB  analysis cache         33%
     25.8 KB  sessions                2%
```

The analysis cache is **compressed**. It holds mostly the list of timestamps for every event,
which alone weighs more than everything else put together (708 KB out of 1.2 MB uncompressed):
it exists so durations and active time can be recomputed with a different `--idle-gap`
**without rereading the transcripts**. Compressing it shrinks it further than throwing half of
it away would — to a little over a quarter, against a third — and costs nobody anything.
Archives written by earlier versions convert themselves, once, on first start.

`--dimentica-testo` compacts the file after deleting: without that, freed pages stay *inside*
the file, and to someone who has just asked to forget something it looks — rightly — as though
nothing happened.

What is deliberately **absent**: an automatic age-based deletion policy. A rule that gets it
wrong, on an archive that is the only remaining copy of deleted conversations, destroys the very
thing it was meant to protect.

---

## Turns

A session can run for days and cost hundreds of dollars: as a unit of measurement it is too
coarse to show *where* that went. The **turn** — one question of yours and everything that
followed, up to the next question — is the grain you actually work in.

```bat
python cam.py --traces --top 20
python cam.py --traces --search reconciliation
```

```
START        PROJECT     SESSION   DUR    REQ  TOOL  CACHE  TOKEN  COST   MODEL            PROMPT
13/08 09:16  gestionale  a1b2c3d4  6m48s   16    15  99.3%  5.28M  $3.46  claude-opus-4-8  fix the CSV export…
13/08 08:52  gestionale  a1b2c3d4  1m59s    1     0   8.8%   309k  $2.95  claude-opus-4-8  do we really need that…
```

Those two rows say something the session total hides: the second turn cost almost as much as the
first **with a single request and no tools at all**. The `CACHE` column explains why — 8.8%
against 99.3%: there the context was rewritten from scratch, and rewriting costs twice the input
price.

In the UI this is the **Traces** tab. Double-clicking a turn opens three views:

- **Trace** — tokens by type, cost, cache hit, models, tools used, subagents involved;
- **Conversazione** — what was said in *that* turn, not in the whole session;
- **Span** — the waterfall: the model request and every tool, with its real duration.

### How a request's duration is derived

The transcript does not record how long a model call took: it writes only the instant the answer
arrived. But the instant it **started** is the previous event — your prompt, or the tool result
that unblocked it. The difference between the two is the real wait. Tools, by contrast, have
explicit start and end: the line invoking them and the line reporting the result.

The consequence is that a turn's time almost never goes into the model: it goes into a tool
waiting for a permission, or a slow command. The waterfall makes that obvious.

### How turns stay in the right place

Grouping uses the **timestamp**, not the position in the file. That is not a detail: Claude Code
re-emits whole segments of history — same uuid, same timestamp — thousands of lines later
(forks, `--resume`, compaction). Grouping by position would dump those lines into the last turn,
which would then absorb the cost of the entire conversation.

Three cases the code handles on purpose, watched over by `test_traces.py`:

- **Sidechain prompts do not open a turn.** In the main transcript they are the orchestrator
  instructing a subagent *inside* a turn that is already open.
- **Subagent turns are not conversation turns.** Their consumption is added to the parent turn
  that contained them, so a turn's cost includes the agents it launched. The `REQ` column counts
  them.
- **Requests preceding the first prompt are not lost**: they land in a prompt-less turn at the
  top of the list.

Checksum: **the cost of the turns adds up exactly to the cost of the session**. Every request
lands in one and only one turn.

### Cache hit

`cache_read / (cache_read + input + cache_write)`: how much of what entered the model came from
cache instead of at full price. Output stays out of the calculation — it is what the model
produces, not what it is given to read.

Across a long conversation this sits reliably above 95%. A low value on a single turn is not a
fault: it is the moment the context was rewritten, and that is where the turn costs.

### Spans, and what is NOT there

A span is a piece of a turn with a start and an end: the `interaction` root, one `llm_request`
per model call, one `tool:<name>` per tool.

Spans are **not in the archive**: they are rebuilt by rereading the transcript when you open a
turn. Keeping them for every session would mean carrying the arguments and results of every
command — megabytes for a detail you look at once. The price is a moment's wait when opening a
turn from a large session.

### Searching, and seeing where

**Search** always covers your prompts, tool names, projects and titles. With
[`archivio.testo`](#archiving-the-text-too) on it also reaches **inside the answers**, with a
full-text index — and then every turn carries **the fragment of conversation the word appears
in**, labelled with who said it:

```
13/08 14:30  a1b2c3d4 #14  risposta  …the «archive» already produces them and they get thrown…
13/08 13:50  a1b2c3d4 #13  tu        …open_«archive»(use_cache, quiet, text)…
```

That is the difference between a search that **narrows a list** and one that **answers**:
without it you would know how many turns contain the word, and not where.

### Exporting turns as a dataset

```bat
python cam.py --traces --search reconciliation --export-turni turns.jsonl
```

One turn per line, with question, answer, models, duration, tools, cost, cache hit and whether
it was interrupted — that is, already the shape in which you evaluate a prompt or compare
models. In the UI it is *Esporta → Turni selezionati in JSONL*.

The selection criterion is not a second set of rules to learn: **it is the filter in front of
you**. Search "reconciliation", narrow to one session, export that.

Answers come from the text archive. Without it they come out `null`, and the command tells you
**how many** turns came out empty: a dataset with half the answers missing is worse than no
dataset, and must not look complete.

---

## Trends and indicators

Totals and rankings say *how much*. They do not say whether it is growing, whether it is
improving, or whether something got worse last week. That is the **Andamento** tab, and
`--trend` on the command line.

```bat
python cam.py --trend
python cam.py --trend --grana mese
python cam.py --trend --finestra 7      :: compare 7 days against the 7 before
```

In the panel the same thing is a chart — one metric at a time, chosen from a menu — plus a row
of **indicators** comparing the recent period with the equally long one just before. The
**Tabella** button shows the same numbers as rows: a chart without its table leaves out anyone
who cannot read it.

### Three choices that change what you read

**Empty periods are drawn.** A week with no work is worth zero and is shown as such: skipping it
would place two distant points side by side and make intermittent use look continuous.

**Sums are filled from zero, levels are not.** Cost and turns are quantities: the area under the
line *is* the quantity, so it starts at zero. Cache hit and median duration are levels: they do
not start from nothing, and squashing them onto a 0–100% axis would flatten a line that actually
moves. They are drawn as a bare line with a fitted axis — and **broken where there is no data**,
because a week with no turns has no cache hit, and drawing a line across it would make it look
measured.

**Never two scales on one chart.** Cost and turns do not belong together: aligning the two axes
would be arbitrary and would invent a correlation the data does not contain.

### Ratios are computed on tokens, not on percentages

A week's cache hit is `cache_read / input tokens` **for the whole week**, not the mean of the
per-turn percentages. The difference is not theoretical: a thousand-token turn at 0% cache and a
million-token turn at 99.99% average out to 50% and are truthfully 99.99%.

### The arrow is coloured only where rising means something

| indicator | if it rises |
|---|---|
| Cache hit, working days, projects | **better** — green |
| Interrupted turns | **worse** — amber |
| Turns, cost per turn, median duration, tools per turn | **depends** — grey |

"Cost per turn" rising can mean bigger problems are being tackled, or that something is being
wasted: without knowing what was being done, a green arrow would be a lie told confidently.
Colour appears only where the direction is certain.

---

## GitHub Copilot

A second source, read from VS Code's chat storage. **On by default**: reading those files is the
same gesture as reading Claude Code transcripts — files already on disk, belonging to the same
person, on the same machine. To turn it off, in `config.json`:

```json
"copilot": { "enabled": false }
```

```
%APPDATA%\Code\User\workspaceStorage\<hash>\chatSessions\*.json    tied to a project
%APPDATA%\Code\User\globalStorage\emptyWindowChatSessions\*.json   opened without a folder
```

### What arrives, and what does not

| | there? |
|---|---|
| turns, times, title, project | **yes** |
| **measured latency** | **yes** — `totalElapsed`, more precise than what we [derive](#how-a-requests-duration-is-derived) for Claude Code |
| model | **yes** — `copilot/claude-sonnet-4.5`, `copilot/auto`, … |
| questions and answers | **yes** |
| tool calls, with names | **yes** — `copilot_readFile`, `run_in_terminal`, … |
| **tokens** | **no** |
| **cost** | **no** |

Tokens are not there and cannot be derived: the only keys that name them are `maxInputTokens`
and `maxOutputTokens`, which are the **model's limits**, not consumption.

**Those figures stay "—", never zero.** Copilot is billed as a flat fee per seat: an
"if it were API" figure computed there would be an invented number, and would be read as
spending. A zero would be another lie — it would claim that turn consumed nothing. The dash says
the only true thing: this is not known here. There is a line under the tables explaining it,
because a "—" in a cost column reads as a zero unless somebody says otherwise.

### Why it is a different source, not a second transcript

Copilot **does not write a transcript**. That is VS Code internal storage, in an undocumented
format that changes with extension versions. On a source like that you cannot say "if the parser
gets it wrong, we reread it": today's file may not exist tomorrow, or may have another shape.

So what gets read lands in the [archive](#the-archive-cam-localdb) as `origine = 'acquisito'`
and is never rebuilt — the same rule that applies to expired transcripts. A file with an
unexpected shape loses that session, it does not bring the program down: the tests deliberately
build broken, empty and malformed ones.

---

## What you actually spent

On a subscription **the only figure that left your account is the monthly fee**. Everything else
measures consumption, not spending.

| column | what it is | did you pay it? |
|---|---|---|
| **`PAGATO`** (`--by-month` view) | the monthly fee | **yes**, the only real amount |
| **`SE FOSSE API`** | what it would have cost at API list price | **no**, never |
| **`QUOTA DEL CONSUMO`** | how much a row weighs in total consumption | a percentage, not a spend |

Currency appears **only where it is real**: in the monthly view.

### Why the fee is not spread across projects

An earlier version did that, month by month, in proportion to consumption. The result was
indefensible: a project worth `$3.80` and 32 minutes of work took **25%** of everything, because
it was the only active one in a month whose fee had been paid anyway.

The flaw is not arithmetic but conceptual: **that fee was not caused by that project.** You
would have paid it without opening Claude Code at all. It was capacity bought and not used.

Unused capacity is read where it makes sense, in the **Mesi** view: a month at `RESA 0.2×` is a
month paid almost for nothing.

### Subscription or API

The switch is `billing.mode` in `config.json`, flippable with `--billing api`:

- **`subscription`** — flat fee. Per-token cost is only a reference;
- **`api`** — metered: per-token cost *is* the charge, and the two columns become one.

Individual projects or sessions can be marked as metered while staying on a subscription, useful
if you launch them with `ANTHROPIC_API_KEY`:

```json
"billing": { "mode": "subscription", "api_projects": ["ProjectX"], "api_sessions": [] }
```

### A session can straddle two months

Attributing it entirely to one month would falsify both, so tokens are **bucketed by month at
parse time**, message by message. The sum of the monthly buckets matches the session total to
the sixth decimal.

---

## Plan limits

There are no "daily" windows: Claude Code exposes **two**, one of **5 hours** and one of
**7 days**, both as a used percentage.

| Where | Source | Freshness |
|---|---|---|
| **Statusline** | the payload Claude Code passes to the command, from API response headers | always current |
| **Tile in the UI** | `~/.claude.json` → `cachedUsageUtilization` | as old as the last update |

Claude Code updates that file **only when it talks to the API**, and considers it stale after an
hour. So the tile **does not show numbers you cannot believe**: a window whose reset has already
passed becomes `—`, and the age of the reading is always written underneath. A `—` does not mean
"zero consumption", it means "that number is no longer valid".

---

## More machines: the team panel

Transcripts cover your machine and nothing else. To see a team you need another source, and
**Claude Code already has one**: it can export its own telemetry over OpenTelemetry, with
nothing of ours running on the seats.

`cam_collector.py` receives that stream and stores it. It accepts **OTLP in JSON encoding over
HTTP**, so no protobuf and no dependencies: stdlib, like everything else.

To try it on a single machine, three commands:

```bat
python cam_collector.py --setup          :: prints the configuration for settings.json
python cam_collector.py                  :: starts the collector on 127.0.0.1:4318
python cam_collector.py --report --by user
```

Then **restart Claude Code**: environment variables are read at startup. The dashboard is at
`http://127.0.0.1:4318/`, and the **Persone** tab appears in the desktop panel.

### Setting it up for a team

The full, ordered runbook — collector first, because its address is what the seats need — is in
the Italian README under
[*Attivarlo, parte prima / parte seconda*](README.md#attivarlo-parte-prima-la-macchina-che-raccoglie).
In outline:

1. **A shared token.** Opening to the network without one leaves the archive writable by anyone
   on it. `python -c "import secrets; print(secrets.token_urlsafe(24))"`
2. **Start it**, with the archive and the pseudonym key **in different folders with different
   permissions** — whoever holds the archive must not be able to re-identify people.
3. **Open the port**, domain profile only.
4. **Make it restart by itself.** `--setup-service` prints the ready-made command for a startup
   shortcut, a scheduled task or a systemd unit. This matters: **a stopped collector leaves no
   trace** — the exporter retries briefly and then gives up, and that interval is never
   recovered.
5. **Verify from another machine** before configuring ten seats:
   `curl "http://host:4318/healthz?token=THE-TOKEN"`
6. **On each seat**: two files (`cam.py` and `cam_agent.py`), the `env` block from
   `--setup` in `~/.claude/settings.json` or in the organisation-wide `managed-settings.json`,
   then **restart Claude Code**, then start `cam_agent.py`.

> **The token is a single one, and it works for reading as well as writing.** The same token you
> hand to every seat so it can send data also opens the dashboard, where each colleague's
> consumption is visible. That is a decision, not a technical detail. Separate read and write
> tokens are on the backlog.

> **There is no TLS.** The collector speaks plain HTTP. On a company network that is a
> defensible choice; on an address reachable from the internet it is not. See
> [PF14](TODO.md#pf14--il-raccoglitore-fuori-dalla-rete-aziendale) for the case of seats
> outside the network, and the private-overlay approach that solves it without new code.

### The three privacy levels

**Telemetry sends `user.email` in the clear, always, and no setting on the seat removes it.**
The level of detail is therefore imposed **at the collector**, when the data is written — which
is also the only point where the choice is verifiable, and the only one under the
administrator's control rather than each machine's.

| `--privacy` | What lands in the archive | What stays possible |
|---|---|---|
| `aggregato` | no personal identifier | cost per model, overall return, cache weight |
| `pseudonimo` *(default)* | a stable keyed code, not the address | dormant seats and limit saturation too |
| `nominativo` | the email address | direct attribution |

Request text **never leaves**: `OTEL_LOG_USER_PROMPTS` stays at `0`. `test_collector.py` verifies
this **on the bytes of the file** — with `pseudonimo` the address must not appear, with
`nominativo` it must — and it checks the `-wal` file too, because until SQLite checkpoints, the
data is there and not in the `.db`.

### Two sources, never added together

Once telemetry is on, the same session exists in both sources. Adding them would count it twice,
so the panel chooses:

| Quantity | From | Why |
|---|---|---|
| cost, tokens, active time, sessions, projects | **transcripts** | they also cover months before telemetry was switched on |
| lines changed, commits, PRs, subagents, tools, MCP | **telemetry** | transcripts do not have them |
| last activity | both | it is a maximum, not a sum |

`cam_agent.py` closes the historical gap: telemetry starts the day you switch it on — on the
development machine, that day it covered **0.00%** of total consumption ($0.10 against
$2,958.85 already on disk). The agent rereads transcripts with the same parser as the panel,
computes the difference against what it has already sent, and sends only that. It opens no ports
and does not listen: it speaks outbound only, so it works identically on site, over VPN, and on
a laptop outside the network.

### Dormant seats are not seen, they are deduced

Whoever does not use Claude Code sends no telemetry, so **they appear nowhere**. To find paid,
never-used seats you have to declare how many you pay for:

```json
"team": { "seats": 8, "fee_per_seat": 30.0, "currency": "EUR", "db": null }
```

```
seat            paid       if it were API   return
anna@x.it     €90.00              $421.00     4.3×
bruno@x.it    €90.00              $180.00     1.9×
carla@x.it    €90.00               $12.00    <0.1×

8 seats paid · 3 used · 5 dormant = €450 over 3 months
```

The *Hai pagato* column total covers all seats, dormant included: summing the visible rows would
give a lower figure and would make exactly the wasted money disappear.

### The third source: the billing export

Telemetry and transcripts measure **consumption**. The Anthropic console is the only one that
knows **spending**.

```bat
python cam_collector.py --import-csv export.csv
python cam_collector.py --riconcilia
```

The export format is undocumented and changes, so columns are recognised from their header names
rather than assumed: `Email`, `Utente`, `actor_email`, `Total Cost`, `Costo`, `amount_usd`,
`Period`, `Mese`, `billing_period` are all understood, as are amounts and dates written the
Italian way (`1.127,50`, `07/2026`) or the American way. If a column stays ambiguous it says so
instead of guessing, and you correct it with `--map user=Member,cost=Amount`.

`--riconcilia` compares the two figures. **They are not supposed to match** — the console bills
the fee, we measure consumption at list value — and what matters is elsewhere:

| What surfaces | Why the comparison is needed |
|---|---|
| Billed but no consumption | Seat paid and never used, or collector not active on that machine |
| Consumption but not billed | Someone is working on a seat nobody is paying for, or the export is partial |

Neither source alone shows these cases. It exits with code 1 if it finds any. In the panel those
rows are flagged with **⚠** next to the name, and the chip at the top isolates them with one
click. Counting them tells you how many; this tells you **which**, which is the only form in
which you can go and ask someone.

### Adoption, month by month

The Persone tab says how many seats are active *now*. Above the table is how it got there — a
chart of active seats per month, with a dashed line for the seats **declared** in `team.seats`.
That line is not decoration: dormant seats stay invisible, so the active curve must be read
against that number and never on its own. The chart appears from two months of data on: with a
single month a trend does not exist, and drawing one would mean inventing it.

### The report to take into a meeting

```bat
python cam_collector.py --relazione                :: to screen
python cam_collector.py --relazione usage.md       :: to a file
```

Markdown with the headline figures, the per-seat table and the per-project one. The caveats are
not in small print at the bottom but **next to the numbers they qualify**. At the end there is
what is **not** collected, and the privacy level is stated at the top: whoever receives the
document should not have to ask you what they are holding.

### Is it working?

```bat
python cam_collector.py --status
```

Answers in one screen: collector reachable, how much data there is, and for each seat **when the
agent last spoke** and **when telemetry last arrived**. Those are two different things that are
easy to confuse: the first says whether the local piece is still alive, the second when somebody
worked. It flags on its own the two cases that otherwise get noticed late and badly — a seat
whose agent has been silent for over a day, and one that sends telemetry but has no agent. Exits
with code 1 if it finds anything, so it can go in an automated check.

### How large the group can be

`python test_carico.py 50 20` simulates fifty seats sending together. Measured on a Windows
laptop: **576 requests per second**, a thousand requests served in 1.7 seconds, worst case 1.5s,
no rows lost or double-counted. For comparison, an agent sends every 15 minutes: fifty seats
make **one request every 18 seconds**. The margin is about four orders of magnitude.

---

## What it reads

```
%USERPROFILE%\.claude\projects\<project>\<uuid>.jsonl                              session
%USERPROFILE%\.claude\projects\<project>\<uuid>\subagents\agent-*.jsonl            subagent
%USERPROFILE%\.claude\projects\<project>\<uuid>\subagents\workflows\wf_*\*.jsonl   workflow
```

Subagent and workflow files are **attributed to the parent session**: the cost of a conversation
includes that of its agents.

### Metrics

- **Duration** — `max(timestamp) − min(timestamp)` of the session.
- **Active** — the sum of intervals between consecutive events shorter than `--idle-gap`
  (default 5 min). Distinguishes "session open for 3 days" from "3 hours of work".
- **Turns** — [from your prompt to the next](#turns). The median duration says more than the
  mean: two sessions left open overnight are enough to move the mean by hours.
- **Cache hit** — share of input tokens that came from cache instead of full price.
- **Messages** — shown as `yours / Claude's`. The second is much higher because every file read,
  command or edit is a message of its own. `tool_result` messages (which Claude Code records as
  `user` type), system messages and placeholders like `[Request interrupted by user]` do not
  count as yours.
- **Tokens** — input, output, cache write 5m, cache write 1h, cache read.
- **Cost** — computed **row by row** with that row's model:

```
cost = input        × IN
     + output       × OUT
     + cache_read   × IN × 0.10     cache read: 10% of input
     + cache_w_5m   × IN × 1.25     cache write TTL 5m: +25%
     + cache_w_1h   × IN × 2.00     cache write TTL 1h: +100%
     + web_search_requests × $0.01
```

### The cache is nearly the whole bill

On a real dataset **98% of the tokens handled** are cache rereads: not new content, but the same
conversation reloaded on every message.

| item | share of cost |
|---|---|
| context reread (×0.10) | ~70% |
| cache write TTL 1h (×2.00) | ~20% |
| generated text (full price) | ~9% |
| non-cached input | ~0% |

Two practical consequences: the cost of a long session is driven by **how much context it drags
along**, not by how many answers it produces; and on a subscription those tokens are not paid in
money but **in limits**, which is the real constraint.

### Consumption beyond the standard window

Extended-context models cost more, and the transcript writes their id **without** the window
suffix: `claude-opus-5`, not `claude-opus-5[1m]`. By name they are indistinguishable — but not
by the numbers. A request that took in 604,000 tokens cannot have gone through a 200,000 window,
and there is nothing to deduce about that.

Those requests are counted, and the tool says so:

```
443 requests exceeded 200k tokens of context (162.14M in total):
they ran on an extended-window model, which the transcript does not distinguish by name
but which is billed at a higher list price.
At the ratio declared in long_context that would be $97.86 more, not included in the totals.
```

The surcharge is **not added to the costs**, and the ratio is configuration:

```json
"finestra_standard": 200000,
"long_context": { "in": 2.0, "out": 1.5 }
```

Two reasons for keeping it out of the totals. First: those multipliers are a price list, price
lists change, and a number you declared that ends up in a cost column becomes true the moment
somebody reads it. Second: folding it in would change the historical numbers of anyone who
updates, without anybody having changed anything. Better a known limit with its measurement next
to it than a silent estimate. With `"in": null` the line does not appear at all.

---

## Two details that change the result

**1. Deduplicating streaming rows.** While answering, Claude Code writes **one row per content
block** (`thinking`, `text`, each `tool_use`), all with the same `message.id`/`requestId` and
with `usage` repeated. Summing every assistant row **inflates the cost by more than double**: on
a real dataset, 7,155 assistant rows corresponded to 3,270 real messages. The tool deduplicates
on `(requestId, message.id)`, keeping the maximum per field.

**2. The rows are not contiguous.** The tempting optimisation is to hold only the last key open
and close the previous ones, but Claude Code re-emits whole segments of history — same `uuid`,
same `timestamp` — thousands of rows later. At peak, 377 keys were measured open at once: that
optimisation inflates tokens by 11.3%.

---

## Configuration

From the UI: the **Configura** button, a panel over five pages — Subscription, Team, Appearance,
Statusline, Price list. Values are validated before writing; changing the theme restarts the
application, because that does not apply hot.

Underneath is a single file, `config.json` next to the script, editable by hand — comments inside
the file are preserved by the panel. **`config.json` is gitignored**: start from
`config.example.json`.

| | Where |
|---|---|
| Plan, cost, currency, theme, price list, statusline, subscription/API switch | `config.json` |
| Period, filter, open tab, Live, geometry, sorting | `%LOCALAPPDATA%\CodeAgentMonitor\gui.json` |

View filters are kept separate from configuration on purpose: they are usage conveniences, and
it makes no sense for them to end up in a file you might version or copy to another machine.

---

## Known limits

- **1M context**: extended-window models have a premium price list, and the transcript records
  the id **without** the `[1m]` suffix. Those requests are counted and declared, but the
  surcharge does not enter the totals.
- **Unknown models**: if a model is not in the price list the cost is 0 and a warning is printed
  with the id to add.
- **Prices change**: `config.json` has an `updated` field, shown at the foot of every report.
- **Partial rows**: during streaming the last line of the file can be incomplete. It is skipped
  without errors.
- **Search reaches into answers only if you enabled `archivio.testo`**, and only from the moment
  you enabled it.
- **Spans are not rebuilt for an archived session**: the waterfall lives in the transcript
  detail, which is not archived.
- **A request's duration is derived, not measured**: if something else sits between the two
  events — a permission granted by hand — that time ends up inside the request.
- **Telemetry starts when you switch it on**: it has no memory of what came before.
- **"Hai pagato" comes from what you declare**, not from an invoice that was read.
- **On Linux the CLI is tested, the UI is not.** The 491 tests run green on Ubuntu 22.04 with
  Python 3.10, and the CLI was genuinely used there. `cam_gui.py` has only been compiled.
- **On macOS nothing has been tested.** The code handles its paths and opens folders with
  `open`, but "should work" is not "works". Reports welcome.

---

## Contributing

Issues and pull requests are welcome. The project is deliberately **dependency-free**: proposals
that add one need a rationale. The code and the comments are in Italian.

Tests, all standard library:

```bat
python test_archivio.py     python test_traces.py       python test_scenario.py
python test_collector.py    python test_statistiche.py  python test_carico.py
python test_copilot.py      python test_resilienza.py
```

---

## Licence

MIT — see [LICENSE](LICENSE).
