# Watsonx Orchestrate Agent Trace Monitor

A guide to the `wxo_traces.py` script shared in this conversation.

The script retrieves execution traces for one IBM watsonx Orchestrate agent, extracts questions and available answer outputs, summarizes timings and explicit errors, and saves JSON snapshots. It monitors existing executions; it does not invoke the agent or create new conversations.

## 1. Setup and first run

Use Python 3.9 or later and install the dependencies in your active environment:

```bash
pip install pandas requests
```



Configure the script:

```python

INSTANCE_ID = "YOUR_INSTANCE_ID"
AGENT_ID = "YOUR_AGENT_ID"
BASE_URL = (
    "https://api.eu-de.watson-orchestrate.cloud.ibm.com"
    f"/instances/{INSTANCE_ID}"
)
```

Compare the resulting URL with wxO → Profile → Settings → API details. Use the exact service instance URL for your environment. Instance ID identifies the wxO environment; agent ID identifies the agent inside it. Do not leave placeholders or empty IDs.

Run from your project directory:

```bash
python wxo_traces.py
```

Stop with Ctrl+C. A one-time test is useful before enabling repeated scans:

```python
RUN_CONTINUOUSLY = False
```

The credentials CSV is plaintext. Keep it private and out of source control. Questions, answers and tool results in the output can also contain sensitive data.

## 2. What a trace means

Imagine one chat:

> User: Which properties were flooded?
> Agent: Properties A and B.
> User: What is their total exposure?
> Agent: EUR 12 million.

The conversation contains these messages. Normally, each user request produces a separate trace. A trace contains internal execution steps, such as model requests and tool calls.

| Identifier | Meaning | Use in this script |
| --- | --- | --- |
| Agent ID | The agent being monitored | Select the agent's traces |
| Conversation ID | The chat containing related messages | Group turns into conversations |
| Trace ID | One request's execution journey | Fetch and identify one execution |
| Observation/span ID | One operation inside an execution | Inspect models, tools, errors and timing |
| Thread ID | Runtime context used for continuing an interaction | Correlate runtime activity |
| Session ID | A grouping identifier whose meaning depends on the layer | Preserve its original field meaning |

In your observed response, the top-level trace `sessionId` matched `output.thread_id`. The `conversationId` was different. The nested `input.session_id` matched the conversation ID instead. Therefore, do not treat every field called session as interchangeable. The script's normalized `thread_id` is copied from the top-level `sessionId`, based on your observed payload; this mapping should be checked if the schema changes.

## 3. What one scan does

A scan is one complete collection cycle performed by our Python script. It is not a special wxO operation.

1. Calculate a fixed UTC time window.
2. Request the first page of traces in that window.
3. Select list entries matching the configured agent ID.
4. Fetch each matching trace's details.
5. Extract observation records, available text outputs, errors and timings.
6. Repeat for subsequent pages.
7. Write the JSON snapshots and print changed results.

With continuous mode enabled, the script waits after this cycle and starts another scan.

The script requests:

```text
POST https://iam.cloud.ibm.com/identity/token
GET  {BASE_URL}/v1/agentops-v3/traces
GET  {BASE_URL}/v1/agentops-v3/traces/{trace_id}
```

For your deployment, listing traces requires `fromTimestamp` and `toTimestamp` as ISO 8601 UTC timestamps. Your API returned a validation error for windows longer than four hours. Those requirements were established by the responses from your instance.

## 4. Configuration parameters

| Parameter | Current default | What changing it does |
| --- | --- | --- |
| `CREDENTIALS_CSV` | Your local credentials path | Changes where the API key is read |
| `INSTANCE_ID` | Must be supplied | Selects the wxO instance in the URL |
| `AGENT_ID` | Must be supplied | Selects which agent's traces are analyzed |
| `BASE_URL` | Frankfurt (`eu-de`) instance URL | Changes the service endpoint |
| `LOOKBACK_HOURS` | `4` | Selects how far back each scan starts; must be greater than zero and at most four |
| `POLL_SECONDS` | `60` | Wait after a scan completes before the next scan |
| `PAGE_SIZE` | `50` | Requested number of trace-list entries per API page |
| `MAX_PAGES` | `100` | Maximum pages fetched per scan |
| `RUN_CONTINUOUSLY` | `True` | Repeat until stopped; `False` runs once |
| `OUTPUT_DIR` | `Path("wxo_output")` | Directory for JSON snapshots, relative to the working directory unless absolute |

### Pages explained

If there are 120 trace entries and `PAGE_SIZE = 50`:

| Page | Entries returned |
| --- | --- |
| 1 | First 50 |
| 2 | Next 50 |
| 3 | Remaining 20 |

A page is a batch of trace entries, not a webpage, chat or message. The listing can include multiple agents; agent filtering happens locally afterward. With 100 pages of 50 entries, the scan inspects at most 5,000 list entries, which are not necessarily 5,000 traces for your agent. The script uses `meta.totalPages` when provided and stops earlier if no entries remain. If the loop exhausts `MAX_PAGES`, it conservatively marks the snapshot `truncated: true`.

### Overlapping windows explained

Approximate example, ignoring scan duration:

| Scan starts | Requested window |
| --- | --- |
| 20:00 | 16:00–20:00 |
| 20:01 | 16:01–20:01 |
| 20:02 | 16:02–20:02 |

The same traces are fetched repeatedly. This allows later observations and completed answers to appear after an execution first becomes visible. Duplicate IDs are removed within a scan. Across scans, files are replaced rather than appended, so overlap does not accumulate duplicate records in these files.

`POLL_SECONDS` is a delay after completion, not a fixed start-to-start interval. If collection takes 15 seconds and the delay is 60 seconds, scan starts are approximately 75 seconds apart.

## 5. How the functions work

| Function/state | Responsibility |
| --- | --- |
| `utc_now`, `iso_utc` | Generate timezone-aware UTC API timestamps |
| `get_token` | Exchange the API key for an IAM token and cache it until shortly before expiry |
| `get_json` | Make authenticated GET requests and retry once after HTTP 401 with a fresh token |
| `unwrap_detail` | Handle a detail object returned directly or inside `data` |
| `walk_dicts` | Walk nested dictionaries and lists |
| `get_observations` | Find expanded observation objects, deduplicate by ID and order by start time |
| `text_from` | Extract common text/message shapes and JSON-encoded message objects |
| `assistant_text` | Find explicitly labelled assistant outputs without tool calls |
| `duration_ms` | Calculate observation duration from start/end timestamps |
| `belongs_to_agent` | Match `agentId`, identity attributes or an `agent_id:` tag |
| `analyze` | Build the normalized record, answer candidates and diagnostic fields |
| `write_json` | Write a temporary JSON file and atomically replace one target file |
| `scan` | Retrieve pages, analyze matching details and write both snapshots |
| `printed_versions` | In-memory fingerprints used to suppress unchanged terminal output |

The API key is read once at startup. If you change the CSV, restart the process. The script automatically obtains replacement IAM tokens using the same API key. It does not require a manually configured `WXO_TOKEN`.

## 6. Why the answer can be missing

Your trace listing contained this root output:

```json
{"streaming": true, "thread_id": "example-thread"}
```

This acknowledges streaming; it is not the agent's answer. The listing's `observations` array contained IDs rather than expanded execution records. The script fetches the individual trace to look for expanded outputs.

Answer extraction tries these sources in order:

1. Text or an assistant message in the root trace output.
2. Output from recognized agent-level observations, excluding names containing tool.
3. Explicit assistant messages in observation outputs, inspected from latest to earliest.

The result is labelled `best_effort`. The parser has not been validated against a complete detailed payload from your agent. An agent-level output or assistant message can still be intermediate. Root text is also accepted heuristically. Do not treat the extracted answer as verified final chat text without inspecting `answer_source` and the raw observation.

If no answer is recognized, the script sets `answer` to null and retains `observation_outputs` and `raw_trace`. Possible causes include an ongoing execution, delayed telemetry, redaction, output not captured, or a schema the parser does not recognize. Increasing the lookback does not itself fix extraction.

## 7. Terminal output

Example only; identifiers and values are illustrative:

```text
Trace: example-trace-001
Question: Which properties were flooded?
Answer: Properties A and B.
Answer source: assistant observation:example-observation
Latency (s): 5.746
Error observations: 0

Saved 1 traces to /your/project/wxo_output | fetch errors=0 | truncated=False
```

A fingerprint suppresses unchanged results during the same process. Because it includes diagnostic outputs and timings, a new print does not necessarily mean a new answer. Restarting clears this in-memory suppression state.

## 8. JSON outputs

Paths are relative to the directory from which you launch Python:

```text
wxo_output/traces.json
wxo_output/metrics.json
```

Both are replaced on each completed scan. Each individual replacement is atomic, but the two files are written separately; a reader could briefly see different scan generations. A failed scan before writing leaves the previous files in place. Check `generated_at` in `traces.json` for freshness.

### traces.json: detailed snapshot

Illustrative abbreviated structure:

```json
{
  "generated_at": "2026-10-27T19:45:00Z",
  "from_timestamp": "2026-10-27T15:45:00Z",
  "to_timestamp": "2026-10-27T19:45:00Z",
  "truncated": false,
  "fetch_errors": [],
  "traces": [
    {
      "trace_id": "example-trace-001",
      "agent_id": "example-agent",
      "timestamp": "2026-10-27T19:39:55.679Z",
      "environment": "draft",
      "conversation_id": "example-conversation",
      "thread_id": "example-thread",
      "question": "Which properties were flooded?",
      "answer": "Properties A and B.",
      "answer_source": "assistant observation:example-observation",
      "answer_extraction": "best_effort",
      "latency_seconds": 5.746,
      "recorded_cost": 0,
      "observation_count": 16,
      "error_observation_count": 0,
      "errors": [],
      "slowest_operations": [],
      "observation_outputs": [],
      "raw_trace": {}
    }
  ]
}
```

The real `raw_trace` holds the merged list/detail payload rather than an empty object. An answer not found is represented by `answer: null`, `answer_source: null`, and `answer_extraction: "not_identified"`.

| Field | Interpretation |
| --- | --- |
| `generated_at` | Snapshot generation time in UTC |
| `from_timestamp`, `to_timestamp` | Window requested by the scan |
| `truncated` | Page cap may have prevented full collection |
| `fetch_errors` | Matching traces whose detail retrieval/analysis failed |
| `timestamp` | Trace creation timestamp, not collection time |
| `question`, `answer` | Extracted text; answer is heuristic |
| `latency_seconds` | API trace latency copied without recomputation |
| `recorded_cost` | Cost value returned by the API; zero does not establish free execution |
| `observation_count` | Expanded observations recognized by the parser |
| `error_observation_count` | Recognized observations with `level == ERROR` |
| `slowest_operations` | Up to five observations ordered by calculated duration |
| `observation_outputs` | Original output values of recognized observations |
| `raw_trace` | Payload for diagnosis and future parser improvements |

A failed observation does not prove the whole request failed; the agent may recover. Zero detected errors does not prove success either. Nested observations can overlap, so do not add their durations to calculate total latency.

### metrics.json: flat dashboard snapshot

```json
[
  {
    "trace_id": "example-trace-001",
    "agent_id": "example-agent",
    "timestamp": "2026-10-27T19:39:55.679Z",
    "environment": "draft",
    "conversation_id": "example-conversation",
    "thread_id": "example-thread",
    "latency_seconds": 5.746,
    "recorded_cost": 0,
    "observation_count": 16,
    "error_observation_count": 0,
    "answer_extraction": "best_effort"
  }
]
```

Metrics intentionally omit questions, answers and raw outputs. They still contain identifiers. There is no snapshot timestamp in this file; use the detailed snapshot's generation time or add a collection timestamp if your ingestion pipeline needs one.

## 9. Parameter recipes

### One-time inspection

```python
LOOKBACK_HOURS = 1
POLL_SECONDS = 60  # Not used after the single scan.
PAGE_SIZE = 50
MAX_PAGES = 100
RUN_CONTINUOUSLY = False
```

### Continuous monitoring with the current script

```python
LOOKBACK_HOURS = 4
POLL_SECONDS = 60
PAGE_SIZE = 50
MAX_PAGES = 100
RUN_CONTINUOUSLY = True
```

This continuously refreshes a rolling snapshot. It does not archive history. Network and API activity can be substantial because every matching detail is re-fetched each cycle.

### Less frequent monitoring

```python
LOOKBACK_HOURS = 4
POLL_SECONDS = 300
RUN_CONTINUOUSLY = True
```

This waits five minutes after each scan and reduces repeated requests. Newly available answers can take longer to appear.

### Shorter recent window

```python
LOOKBACK_HOURS = 0.5  # Last 30 minutes.
POLL_SECONDS = 60
RUN_CONTINUOUSLY = True
```

This reduces data volume but excludes older traces, including long-running requests that started outside the window.

### Longer history

Do not set `LOOKBACK_HOURS = 24`: your API rejects windows longer than four hours. Fetch a day in six consecutive four-hour windows, paginating each window, then merge by trace ID. This requires code changes; the current script does not implement historical backfill.

## 10. Running indefinitely on your Mac

`RUN_CONTINUOUSLY = True` repeats while the process remains alive. It does not automatically restart after reboot or a process crash. Handled network/API errors are printed and retried on the next cycle, but unexpected exceptions may terminate the process.

For an interactive session, macOS `caffeinate` can inhibit idle system sleep while the command runs:

```bash
caffeinate -i python -u wxo_traces.py
```

Do not rely on this to keep a closed-lid laptop running. Power loss, shutdown and network interruptions still matter.

To detach from the terminal on macOS/Linux:

```bash
nohup python -u wxo_traces.py > wxo_monitor.log 2>&1 &
```

Record the PID printed by the shell. Stop that process with:

```bash
kill YOUR_RECORDED_PID
```

`nohup` does not prevent sleep or restart the application after a reboot. Terminal logs grow without rotation even though the JSON snapshots are replaced. Use log rotation and a service supervisor for unattended operation. For a durable deployment, use an always-on host with automatic restart, controlled credentials and durable storage.

## 11. Continuous history: improvements not implemented yet

Changing parameters alone cannot turn this collector into a reliable historical archive. The following design requires code changes:

1. Perform an initial four-hour collection.
2. Persist a checkpoint only after successful collection and durable storage.
3. On later cycles, request since the checkpoint with a small overlap, such as five minutes.
4. Split windows exceeding four hours after downtime into API-compatible chunks.
5. Upsert records using `(instance_id, trace_id)` as a unique key.
6. Keep unfinished traces in a separate pending set and re-fetch them until completion, even after their creation timestamps leave the overlap window.
7. Retain errors for retry; do not advance collection state past unprocessed data without tracking it.
8. Use bounded retries/backoff for transient failures and alert on repeated authentication or collection failures.

The five-minute overlap is an example, not a guaranteed ingestion-delay bound. Late traces can require periodic reconciliation over a longer retained interval. The current script has no durable checkpoint, pending set, database, reconciliation job or archive. Its fingerprints also remain in memory as new traces appear, so their dictionary can grow during a long run and would benefit from pruning.

## 12. Grafana integration

The files prepare data for ingestion; Grafana does not automatically discover local JSON files. A configured data source/backend or suitable HTTP-accessible JSON source is required. No Grafana integration is installed or configured by this script.

For a small prototype, expose the flat metrics through a controlled HTTP endpoint compatible with your Grafana JSON data source. Avoid serving the entire output directory publicly because `traces.json` contains full conversation and tool data.

For historical dashboards, ingest normalized records into a durable database or observability backend that Grafana can query. Upsert by trace ID rather than appending a new trace every time a polling cycle sees the same execution.

| Dashboard | Fields/calculation |
| --- | --- |
| Latency trend | `timestamp` and `latency_seconds` |
| Request volume | Count distinct `trace_id` per time bucket |
| Error-observation trend | `timestamp` and `error_observation_count` |
| Executions containing errors | Count distinct traces where error observation count is greater than zero |
| Conversation activity | Group by `conversation_id` |
| Answer extraction coverage | Fraction with `answer_extraction == best_effort`; this is parser coverage, not answer quality |
| Environment comparison | Filter/group by `environment` |

Do not label error-observation counts as confirmed request failure rates. Avoid putting trace IDs, conversation IDs or answer text into high-cardinality metric labels; retain them in queryable trace/log/database records instead.

## 13. Conversation reconstruction

Group stored records by `conversation_id`, sort by `timestamp`, then display `question` and the extracted `answer`. Preserve trace IDs for drill-down. This reconstructs only the turns available in the collected telemetry. The current script does not call a dedicated conversation-history API and cannot guarantee complete chat history.

## 14. Troubleshooting

| Symptom | What to check |
| --- | --- |
| URL contains `YOUR_INSTANCE_ID` | Replace placeholders and construct the URL using the actual variable |
| HTTP 400: timestamps required | Supply `fromTimestamp` and `toTimestamp` |
| HTTP 400: range too large | Keep each request window at most four hours |
| HTTP 401 | API key validity and instance access; script retries once with a new IAM token |
| HTTP 403 | Permissions for the key identity and target instance |
| HTTP 500 | Exact endpoint and response body; correct configuration does not rule out a service issue |
| HTTP 200 with `data: []` | Window, instance, capture/monitoring configuration and telemetry delay; send a test message |
| Matching agent yields no records | Actual `AGENT_ID`, draft/live context, and list metadata identity fields |
| Answer is null | Inspect `observation_outputs` and `raw_trace`; trace may be incomplete or use a different schema |
| Zero observations | Detail response may contain only IDs or an unrecognized envelope |
| Output stays unchanged | Check process state, errors and `generated_at`; unchanged console text may simply be suppressed |
| `truncated: true` | Increase page cap cautiously or reduce window and verify API load |
| Trace disappears from file | It may have aged out of the rolling window, failed detail retrieval, or been omitted by a capped scan |
| API key changed in CSV but not used | Restart; the CSV is read once at startup |


