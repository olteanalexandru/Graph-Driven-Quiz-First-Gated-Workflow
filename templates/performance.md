# ITEM-000 workload and performance

## Workload
| Path | Runs | Data size (typical / max) | Latency budget | Memory | Analysis | Basis |
|------|------|---------------------------|----------------|--------|----------|-------|
|      |      |                           |                |        | no:      |       |

Basis: a decision (D#), an assumption (A#), a measurement, or the graph (callers, where
it renders). Analysis is "yes" when any of these holds; otherwise write "no" and the
one-line reason (for example "no: n <= 88, once per answer"):
- it runs per frame, keystroke, render or audio buffer, or more than about 10 times a second;
- n can pass about 1,000 per call, or grows with user data without a limit;
- the latency budget is 100 ms or less, or the path runs inside a 16.7 ms frame (60 Hz);
- memory is tight (edge or worker runtime, mobile, an audio thread) or grows with use;
- a network or database call sits inside a loop.

## Analysis
Only the rows marked yes: the operations, their time and space complexity at the
expected and the maximum n, the structure chosen and the one rejected.

## Budgets
Measurable budgets for the critical paths: user-visible hot paths and every row marked yes.
| ID | Path | Metric | Budget | Measured by |
|----|------|--------|--------|-------------|
| PB1 |     |        |        | test `PB1 ... @perf @item:ITEM-000` |

Reference values: Core Web Vitals "good" at the 75th percentile is LCP 2.5 s or less,
INP 200 ms or less and CLS 0.1 or less; a task over 50 ms blocks input.
