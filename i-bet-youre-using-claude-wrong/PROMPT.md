# Claude Code usage audit prompt

Paste everything below the line into Claude Code (terminal, desktop app, or IDE).
It needs Claude Code, not the claude.ai chat, because it reads the local session
logs on your machine. Nothing leaves your machine.

---

Audit how I use Claude Code and show me where my tokens go. Build one
interactive HTML report with charts and the top things I should change.

## Rules

- Read only. Do not change any file except the report and your script.
- Use Python 3 standard library only. No installs, no network.
- Privacy: never put my prompt text, Claude's replies, file contents or command
  text into the report. Show counts and sizes only. Label projects "Project A",
  "Project B"... and files "file 1 (.py)", "file 2 (.md)"... MCP tools show
  as "MCP server 1: <tool>", never the raw `mcp__<id>__` prefix. Real names may
  be embedded only behind a "Show names" checkbox that is off by default, so
  screenshots are safe. A project's real name is the last folder of the `cwd`
  field in its log lines.
- Do not spend my tokens reading the logs yourself. Write one script that reads
  the logs, writes the HTML report, and prints a short summary (under 200
  lines). Read only that summary.

## Step 1: find the logs

- Root: `$CLAUDE_CONFIG_DIR/projects/` if set, else `~/.claude/projects/`
  (Windows: `%USERPROFILE%\.claude\projects\`).
- Main session logs: `<root>/*/*.jsonl`. Subagent logs: every `*.jsonl` under
  `<root>/*/<session-id>/`, at any depth (glob recursively; many sit in
  `subagents/workflows/...`). A subagent log belongs to the session in its path.
- A session's last activity = newest file mtime across its main log and its
  subagent logs. Use sessions active in the last 30 days. If fewer than 10,
  use the newest 10.
- Skip the session running this audit: the main log with the newest mtime.
- Skip sessions with no model calls.

## Step 2: parse correctly

- Each line is one JSON object. Skip lines that fail to parse.
- **Thread** = one log file. The main log and each subagent log are separate
  threads with their own context. Compute everything that depends on "later
  calls" per thread.
- **Model call** = a line with `type == "assistant"` and `message.usage`. One
  call spans several lines with the same `requestId` (fallback `message.id`).
  Count each call once, globally across all files: a resumed or forked session
  can copy calls into a second file. Keep the copy in the file whose first
  timestamp is earliest. Do the same for tool calls and results by their id.
- Per call, from `message.usage`: `input_tokens` (fresh input),
  `cache_creation_input_tokens` (cache write), `cache_read_input_tokens`
  (cache read), `output_tokens`. Context = input + cache write + cache read.
  Model = `message.model`; skip calls whose model is `<synthetic>`.
- **Tool call** = a `tool_use` block in an assistant `message.content` (`name`,
  `input`, `id`). **Tool result** = a `tool_result` block in a `type == "user"`
  line, matched by `tool_use_id`. Result tokens = text characters / 4. Count
  each image in a result as 1,600 tokens, never by its base64 length.
- **User prompt** = a `type == "user"` line in a main log, not `isMeta`, not
  `isSidechain`, whose content is a string or has a `text` block, and is not
  only tool results. Ignore text that starts with `<command-` or
  `<local-command-` or is only a `<system-reminder>`.
- **Compaction** = a `type == "system"` line with `subtype == "compact_boundary"`.
- Time = each line's `timestamp`.

## Step 3: cost units

Plans differ, so show no dollars. Cost units use the real price ratios: fresh
input x1, cache write x1.25, cache read x0.1, output x5. Show every cost and
saving as a percent of my total cost units.

## Step 4: the checks

Each check gets: status (good / watch / fix), the number behind it, the rough
saving (percent of total cost, computed with the formula given), and a fix.
Show savings as "about X%" and call them rough.

1. **Marathon sessions.** Number: share of cost in calls with context above
   200k. Fix above 40%, watch above 20%. Saving: for each such call, the cost of
   (context − 120k) x 0.1.
2. **One session eats everything.** Number: share of total cost in the biggest
   session. Fix above 60%, watch above 40%. Saving: none, it points at check 1.
3. **Cold restarts.** A gap above 60 minutes between two calls in one thread,
   then a call with cache write above 50k. Number: count, and their
   cache-write cost as a share of total. Fix above 5%, watch above 2%. Saving:
   half of that cost (a fresh session from a handoff note is about half the
   size).
4. **Carry cost of big tool results.** Each result is re-sent on every later
   call in its thread. Carry cost = result tokens x later calls in the thread x
   0.1. Number: top 10 carry cost as a share of total. Fix above 10%, watch
   above 5%. Saving: half of the top 10.
5. **Re-reads.** Same file read 2+ times in one thread, or the same command run
   3+ times. Wasted = every repeat after the first. Number: share of all tool
   result tokens. Watch above 3%, fix above 10%. Saving: wasted tokens x 1.25,
   as a share of total.
6. **No delegation.** Number: subagent threads per 100 main-thread calls, and
   sessions with more than 100k tokens of main-thread tool results and no
   subagent. Fix if 3 or more such sessions, watch if 1 or 2. Saving: half the
   carry cost of those sessions.
7. **Noisy commands.** Bash results above 5k tokens. Number: count per session
   on average. Watch above 3, fix above 10. Saving: half their carry cost.
8. **Startup weight.** Number: median context of the first call of each main
   thread (system prompt, CLAUDE.md, memory, MCP tool lists). Watch above 30k,
   fix above 50k. Saving: (median − 25k) x all calls x 0.1, as a share of total.
9. **Cache health.** Number: cache read / context, over all calls. Fix below
   80%, watch below 90%. Saving: none.
10. **Wordy output.** Number: output share of cost, and output tokens per user
    prompt in main threads only. Watch above 20%, fix above 30%. Saving: 30% of
    output cost.
11. **Compactions.** Number: count. Watch at 1 or more, fix at 5 or more per
    10 sessions. Saving: none, it points at check 1.
12. **Model mix.** Calls and cost per model. No status, unless one session
    switched models 4 or more times (each switch rebuilds the cache): then
    watch.

Grade: start at 100, minus 15 per "fix" and 5 per "watch". A at 90 or more, B
at 75, C at 60, D at 45, F below 45.

## Step 5: build the report

Save one self-contained file to `~/claude-usage-report.html`. No external
scripts, fonts or images. Draw charts with inline SVG and plain JavaScript.
Embed the data as one JSON object. It must work offline, in light and dark mode
(`prefers-color-scheme`), and on a 375px-wide phone with no sideways scroll.

Sections, in this order:

1. **Header.** Grade (big), date range, sessions, model calls, total cost
   units, cache hit rate, subagent threads. One-sentence verdict.
2. **Top 3 things to change.** The three non-good checks with the biggest
   saving. Each: the problem in one sentence, my number, one concrete habit to
   fix it, the rough saving.
3. **Where the cost goes.** Stacked bar per session (fresh input, cache write,
   cache read, output) for the 30 biggest sessions plus one "all others" bar.
   Show a legend with a color swatch per part. Hover shows numbers. Click a
   bar to select it for section 4.
4. **Session detail.** Line chart of context size per call in the selected
   session's main thread (default: the biggest), thinned to at most 300 points,
   with a dashed line at 200k and markers at cold restarts and compactions.
   Also its duration, prompts, calls and subagent threads.
5. **Cost by context size.** Bar chart of cost share by context band: under
   50k, 50-100k, 100-200k, 200-400k, over 400k.
6. **Biggest carry costs.** Horizontal bars for the top 10 tool results (tool,
   file label or "command", size, later calls).
7. **All checks.** Table: check, status chip, number, rough saving, fix. Click
   a row to expand one short paragraph on why it costs.
8. **What you already do well.** Every "good" check, one line each.
9. **Footer.** How the numbers were made, in five lines. Savings are rough.

Fix advice (use what fits the numbers):

- Marathon, one big session, compactions: one session per task step. Before
  you change topic or pass ~200k, ask Claude to write a handoff note (goal,
  done, state now, next step, traps) to a file. Start a fresh session that
  reads it.
- Cold restarts: after a break of more than an hour, start fresh from a
  handoff note. Do not continue a huge session.
- Carry cost, no delegation: ask Claude to use a subagent for big reads (many
  files, long logs, big dumps). The subagent reads; only its short answer
  enters your chat. Read parts of big files, not whole files.
- Re-reads: tell Claude to grep or read line ranges instead of re-reading
  whole files.
- Noisy commands: pipe builds and tests through `tail -30` and keep error
  lines.
- Startup weight: keep CLAUDE.md to rules that matter in every session, and
  turn off MCP servers this project does not use.
- Wordy output: set a short output style, or say "answer in 5 lines".
- Model mix: pick the model when the session starts; do not switch mid-way.

## Step 6: tell me

Reply in the chat with no more than 8 lines: the grade, the top 3 fixes with
their rough saving, and the report path. Then open the report in my browser if
you can.
