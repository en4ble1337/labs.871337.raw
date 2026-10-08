# I Bet You Are Using Claude Code Wrong

A paste-in prompt that audits your own Claude Code usage and builds an
interactive HTML report with a grade, charts, and the top things to change.

**Made:** 2026-10-08. **Tested on:** Claude Code session logs, 277 sessions over
six weeks, run three times by a fresh Sonnet agent with no other context.

## The finding, in short

Most of the cost is not the work. It is carrying the chat.

- Every step sends the whole chat again. In my logs, 60% of all cost was spent
  in calls above 200k tokens of context.
- My cache hit rate was 97.5%. The cache was fine; the sessions were too long.
- A session starts at about 62k tokens before I type anything (system prompt,
  CLAUDE.md, memory, MCP tool lists).
- After a break of more than an hour the cache expires, and the whole chat is
  written to it again. That happened 200 times.
- My grade: F.

## Contents

| File | What it is |
| --- | --- |
| [`PROMPT.md`](PROMPT.md) | The prompt. Paste everything below its line into Claude Code. It reads only local logs, needs only Python 3, and writes `~/claude-usage-report.html`. |
| [`example-report.png`](example-report.png) | My own report. Projects and files are anonymized by default. |
| [`post-image.png`](post-image.png) | The grade and top 3 fixes, cropped for the post. |

## Notes

- Savings in the report are rough estimates, shown as a share of your total
  cost units (fresh input x1, cache write x1.25, cache read x0.1, output x5).
  No dollars, because plans differ.
- The log format is not a public API and may change. The prompt tells Claude
  how to parse it today; if a check looks wrong, ask Claude to re-check the
  format first.
