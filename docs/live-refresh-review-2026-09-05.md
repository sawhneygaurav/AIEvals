# Live refresh and trace review

## 5 September 2026 · current implementation

**Result: a new validated live report was saved and displayed in the app.** The successful run took
**39.66 seconds**, compared with **120.08 seconds** in the earlier baseline.
That is a 67% reduction in observed elapsed time across these two runs. Cache
reuse, faster searches, model latency and the absence of a repair call all affect
this comparison; it is not an isolated measurement of the cache improvement.

- Research cutoff: **2026-09-05**.
- Successful run: `c5f2bf2b70804a82b6d505e9faa58821`.
- Report: `5804e0cb-e85b-4e2f-9334-c14c8af56bd1`.
- Saved at **15:49 IST** (10:19 UTC).
- Provider/model: **Nebius / moonshotai/Kimi-K3**.
- Four companies ranked; independent saved-report publication scan passed with zero issues.

[Beginner code guide](CODE_GUIDE.md) · [Machine-readable review](live-refresh-review.json)

## What was reviewed

All event lines in the retained run-log directories were parsed. Span starts
and finishes, parent references, contiguous event sequences, HTTP/provider
failures, cache events, evidence coverage, audit/publication results and terminal
statuses were checked. The saved report was loaded through ReportStore and
independently rescanned using the publication validator.

The successful refresh contains **727 events and 315 matched timed spans**. It
has no orphaned finishes, missing parent IDs, unfinished spans, errors,
diagnostic warnings, failed model attempts or retries. Four Fundamentals
coverage events are complete; the evidence audit and all three publication-scan
events pass.

## Where the time went

| Company | Source research | Shared dossier | Whole company branch |
|---|---:|---:|---:|
| PNGJL | 7.67s | 19.20s | 26.95s |
| KALYANKJIL | 7.51s | 30.70s | 38.30s |
| SENCO | 7.81s | 22.25s | 30.14s |
| THANGAMAYL | 12.50s | 22.06s | 34.64s |

Company branches overlap. Parent spans include their children, so these
durations must not be added across companies or nested operations. KALYANKJIL
was the final company to finish. Its provider request took 30.63 seconds. All
book work, final checks and persistence fit into approximately 1.3 seconds
after the last company.

All **11 public PDFs** received server revalidation and **11 excerpt-cache hits**.
There was no PDF extraction on this run. The graph made 28 search requests and
four dossier model calls. The aggregate model-slot wait was about 0.02 seconds,
so more model concurrency would not address the dominant delay observed here.

## The failed attempt and the fixes

The first refresh in this task, `72ed0928…`, completed in 42.25 seconds but was
rejected. SENCO initially lacked cash-flow evidence. Its targeted repair
supplied a valid cumulative CFO/PAT ratio, yet the final Fundamentals block
was missing interest-coverage/liquidity protection evidence. Book coverage fell
to 75%, so no new ranking replaced the existing validated report.

A reproducible merge defect was found: the per-response 20-observation schema
limit was being reapplied to the union of initial evidence and repair. Adding
ten rows of five-year cash/PAT history could remove valid protection metrics
from the end of the list. A regression test failed before the fix and passed
after it. The internal union now retains every distinct observation and runs
the existing provenance, date, unit, basis and coverage checks. The model
response limit and scoring weights remain unchanged. Coverage is now also
logged after a repair.

A second diagnostic defect labelled `SystemExit(2)` as an HTTP error because
its numeric `code` was interpreted as an HTTP status. New logs use
`process_exit` and `exit_code`; HTTP classifications are restricted to valid
HTTP status numbers. The original failed trace is preserved as recorded,
including its old label.

After both fixes and **208 passing tests**, a fresh end-to-end live run
published successfully. It needed no model retry or additional Fundamentals
repair. The regression test, rather than that no-repair run alone, verifies
the repaired merge path.

The failed attempt’s 16 publication issues are not 16 separate network
failures. Several are downstream consistency checks caused by deliberately
clearing ranks and final scores when the evidence audit fails. This can make
rejected-run logs noisy; the SENCO evidence/coverage failure is the primary
issue to investigate.

## Retained run history

The machine-readable review includes every available demo and live run,
including older diagnostics written before all current summary fields existed.
Missing fields in those old logs are treated as unavailable, not as zero.
Historical failures are preserved rather than rewritten to make the history
look clean. The three live runs are:

| Run prefix | Outcome | Elapsed | Meaning |
|---|---|---:|---|
| `08352805` | Succeeded | 120.08s | Earlier live baseline, before PDF caching |
| `72ed0928` | Unvalidated | 42.25s | Cached refresh rejected for SENCO evidence coverage |
| `c5f2bf2b` | Succeeded | 39.66s | New validated report after the tested merge fix |

The previously observed demo interruption `b830d436…` occurred while
opening/hashing the private book file and was stopped during diagnosis.
It is not an unfinished current live run. Other historical demo statuses
are retained in the review JSON without being reinterpreted as live failures.

## Limits that remain visible

- All four book scores have **95% weighted evidence coverage**. Credit-rating
  evidence is missing; a passed 80% minimum-coverage gate is not 100% completeness.
- Token usage fields are **null** in these streamed provider responses. Cost
  and token efficiency cannot be established from this trace.
- Technical observations are dated **4 September**, the latest trading-day
  data used by the run. Fundamental and valuation dates vary by observation.
- Live refresh retrieves current sources but does not guarantee the newest
  filing exists in every issuer catalogue. Facts are accepted within explicit age windows.
- Audits check consistency, completeness and references; they do not
  independently establish every source claim or calibrate model confidence.
- Live valuation remains the documented ratio/growth heuristic. Full
  scenario fair values and general branch resume are not implemented.
- Some citation URLs containing spaces render incorrectly in the app and
  Markdown report; their full URLs remain in the structured report JSON.
- The direct collector's manual-reference policy does not exclude You.com
  search snippets from the same domains. The code guide distinguishes these paths.

The supported conclusion is **this refresh passed the implemented checks**,
with the above limitations. Describing the whole system as perfect would
overstate what was verified.

## Evidence files

- [Successful summary](../outputs/run_logs/live/c5f2bf2b70804a82b6d505e9faa58821/summary.json)
- [Successful event timeline](../outputs/run_logs/live/c5f2bf2b70804a82b6d505e9faa58821/events.jsonl)
- [Rejected-attempt summary](../outputs/run_logs/live/72ed092816e44160bbd9e9a240655f90/summary.json)
- [Published report](../outputs/report_store/live/latest.json)
- [Earlier document-only benchmark](../outputs/benchmarks/pdf-cache-20260905T100308Z.json)

The published-report link follows the latest report and can change on a
future refresh. The run IDs and report ID above identify this review’s
specific snapshot.
