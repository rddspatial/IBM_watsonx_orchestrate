import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pandas as pd
import requests


# ================= CONFIGURATION =================

INSTANCE_ID = "" # wxo instance id
AGENT_ID = "" # AGENT ID
IBM_CLOUD_API_KEY = "" # YOUR IBM CLOUD API KEY

BASE_URL = (
    f"https://api.{region}.watson-orchestrate.cloud.ibm.com"  # check your wxo region (e.g., eu-de or us-south)
    f"/instances/{INSTANCE_ID}"
)


LOOKBACK_HOURS = 4       # API allows at most four hours per request. You can change this based on the requirement. 
POLL_SECONDS = 60
PAGE_SIZE = 50
MAX_PAGES = 100
RUN_CONTINUOUSLY = True

OUTPUT_DIR = Path("wxo_trace_output")

# =================================================

for name, value in {
    "INSTANCE_ID": INSTANCE_ID,
    "AGENT_ID": AGENT_ID,
}.items():
    if not value.strip() or value.startswith("PASTE_"):
        raise ValueError(f"Configure {name} first.")

if not 0 < LOOKBACK_HOURS <= 4:
    raise ValueError("LOOKBACK_HOURS must be greater than 0 and at most 4.")




OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
session = requests.Session()

_token = None
_token_refresh_at = 0
printed_versions = {}


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_token():
    global _token, _token_refresh_at

    if _token and time.time() < _token_refresh_at:
        return _token

    response = session.post(
        "https://iam.cloud.ibm.com/identity/token",
        data={
            "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
            "apikey": IBM_CLOUD_API_KEY,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()

    _token = payload["access_token"]
    _token_refresh_at = (
        time.time() + max(0, int(payload["expires_in"]) - 60)
    )
    return _token


def get_json(path, **params):
    global _token

    for attempt in range(2):
        response = session.get(
            f"{BASE_URL}/v1/agentops-v3/{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {get_token()}",
                "Accept": "application/json",
            },
            timeout=60,
        )

        if response.status_code == 401 and attempt == 0:
            _token = None
            continue

        if not response.ok:
            raise RuntimeError(
                f"HTTP {response.status_code}: {response.text[:2000]}"
            )

        return response.json()


def unwrap_detail(payload):
    if not isinstance(payload, dict):
        raise ValueError("Unexpected trace-detail response.")

    if isinstance(payload.get("data"), dict):
        return payload["data"]

    return payload


def walk_dicts(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_dicts(value)


def get_observations(detail):
    records = {}
    for node in walk_dicts(detail):
        if (
            node.get("id")
            and node.get("type")
            and ("startTime" in node or "start_time" in node)
        ):
            records[node["id"]] = node

    return sorted(
        records.values(),
        key=lambda item: str(
            item.get("startTime") or item.get("start_time") or ""
        ),
    )


def text_from(value):
    """Extract common message text without stringifying arbitrary metadata."""
    if isinstance(value, str):
        # Some exports serialize message objects as JSON strings.
        try:
            decoded = json.loads(value)
        except (ValueError, TypeError):
            return value
        if isinstance(decoded, (dict, list)):
            return text_from(decoded)
        return value

    if isinstance(value, list):
        parts = [text_from(item) for item in value]
        return "\n".join(part for part in parts if part)

    if isinstance(value, dict):
        for key in ("text", "content", "message", "messages", "answer"):
            if key in value:
                result = text_from(value[key])
                if result:
                    return result

        # OpenAI-style output.
        if isinstance(value.get("choices"), list):
            return text_from(value["choices"])

    return ""


def assistant_text(value):
    """Only accept explicitly labelled assistant messages."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return ""

    messages = []
    for node in walk_dicts(value):
        if node.get("role") == "assistant" and not node.get("tool_calls"):
            text = text_from(node.get("content"))
            if text:
                messages.append(text)

    return "\n".join(dict.fromkeys(messages))


def duration_ms(observation):
    start = observation.get("startTime") or observation.get("start_time")
    end = observation.get("endTime") or observation.get("end_time")

    if not start or not end:
        return None

    try:
        start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return round((end - start).total_seconds() * 1000, 2)
    except (ValueError, TypeError, AttributeError):
        return None


def belongs_to_agent(trace):
    attrs = trace.get("metadata", {}).get("attributes", {})
    ids = {
        trace.get("agentId"),
        attrs.get("agent.id"),
        attrs.get("wxo.facet.agent_id"),
    }
    tags = trace.get("tags") or []
    return AGENT_ID in ids or f"agent_id:{AGENT_ID}" in tags


def analyze(listed, detail):
    # Retain list fields if detail omits them.
    trace = {**listed, **detail}
    observations = get_observations(detail)

    outputs = []
    for item in observations:
        if item.get("output") is not None:
            outputs.append({
                "observation_id": item["id"],
                "name": item.get("name"),
                "type": item.get("type"),
                "output": item["output"],
            })

    answer = ""
    answer_source = None

    # Root output may contain an actual answer or just streaming metadata.
    root_output = trace.get("output")
    answer = assistant_text(root_output) or text_from(root_output)
    if answer:
        answer_source = "trace.output"

    if not answer:
        # Prefer an agent-level operation; ignore tool operations.
        for item in reversed(observations):
            name = str(item.get("name", "")).lower()
            kind = str(item.get("type", "")).upper()
            agent_level = (
                kind == "AGENT"
                or "invoke_agent" in name
                or "agent.task" in name
            )
            if agent_level and "tool" not in name:
                candidate = text_from(item.get("output"))
                if candidate:
                    answer = candidate
                    answer_source = f"agent observation:{item['id']}"
                    break

    if not answer:
        for item in reversed(observations):
            candidate = assistant_text(item.get("output"))
            if candidate:
                answer = candidate
                answer_source = f"assistant observation:{item['id']}"
                break

    errors = [
        {
            "observation_id": item["id"],
            "name": item.get("name"),
            "message": item.get("statusMessage"),
        }
        for item in observations
        if str(item.get("level", "")).upper() == "ERROR"
    ]

    timings = [
        {
            "name": item.get("name"),
            "duration_ms": duration_ms(item),
        }
        for item in observations
        if duration_ms(item) is not None
    ]

    return {
        "trace_id": trace.get("id"),
        "agent_id": AGENT_ID,
        "timestamp": trace.get("timestamp"),
        "environment": trace.get("environment"),
        "conversation_id": trace.get("conversationId"),
        "thread_id": trace.get("sessionId"),
        "question": text_from(trace.get("input")),
        "answer": answer or None,
        "answer_source": answer_source,
        "answer_extraction": "best_effort" if answer else "not_identified",
        "latency_seconds": trace.get("latency"),
        "recorded_cost": trace.get("totalCost"),
        "observation_count": len(observations),
        "error_observation_count": len(errors),
        "errors": errors,
        "slowest_operations": sorted(
            timings, key=lambda item: item["duration_ms"], reverse=True
        )[:5],
        "observation_outputs": outputs,
        "raw_trace": trace,
    }


def write_json(path, data):
    """Replace files atomically so readers don't see half-written JSON."""
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def scan():
    end = utc_now()
    start = end - timedelta(hours=LOOKBACK_HOURS)
    collected = {}
    fetch_errors = []
    truncated = False

    for page in range(1, MAX_PAGES + 1):
        payload = get_json(
            "traces",
            fromTimestamp=iso_utc(start),
            toTimestamp=iso_utc(end),
            page=page,
            limit=PAGE_SIZE,
        )

        entries = payload.get("data")
        if not isinstance(entries, list):
            raise ValueError("Expected a trace list in response['data'].")

        for listed in entries:
            if not belongs_to_agent(listed):
                continue

            trace_id = listed["id"]
            if trace_id in collected:
                continue

            try:
                detail = unwrap_detail(
                    get_json(f"traces/{quote(trace_id, safe='')}")
                )
                record = analyze(listed, detail)
                collected[trace_id] = record

                # Print again only if the analysis changes.
                visible = {
                    key: value for key, value in record.items()
                    if key != "raw_trace"
                }
                fingerprint = hashlib.sha256(
                    json.dumps(visible, sort_keys=True).encode()
                ).hexdigest()

                if printed_versions.get(trace_id) != fingerprint:
                    print(f"\nTrace: {trace_id}")
                    print("Question:", record["question"])
                    print(
                        "Answer:",
                        record["answer"]
                        or "[Not identified; inspect observation_outputs in JSON]",
                    )
                    print("Answer source:", record["answer_source"])
                    print("Latency (s):", record["latency_seconds"])
                    print("Error observations:", record["error_observation_count"])
                    printed_versions[trace_id] = fingerprint

            except (requests.RequestException, RuntimeError, ValueError) as exc:
                fetch_errors.append({
                    "trace_id": trace_id,
                    "error": str(exc),
                })
                print(f"Could not fetch trace {trace_id}: {exc}")

        total_pages = payload.get("meta", {}).get("totalPages")
        if not entries or (
            total_pages is not None and page >= int(total_pages)
        ):
            break
    else:
        truncated = True

    records = sorted(
        collected.values(),
        key=lambda item: item.get("timestamp") or "",
    )

    write_json(OUTPUT_DIR / "traces.json", {
        "generated_at": iso_utc(utc_now()),
        "from_timestamp": iso_utc(start),
        "to_timestamp": iso_utc(end),
        "truncated": truncated,
        "fetch_errors": fetch_errors,
        "traces": records,
    })

    metric_fields = [
        "trace_id",
        "agent_id",
        "timestamp",
        "environment",
        "conversation_id",
        "thread_id",
        "latency_seconds",
        "recorded_cost",
        "observation_count",
        "error_observation_count",
        "answer_extraction",
    ]
    write_json(OUTPUT_DIR / "metrics.json", [
        {field: record.get(field) for field in metric_fields}
        for record in records
    ])

    print(
        f"\nSaved {len(records)} traces to {OUTPUT_DIR.resolve()}"
        f" | fetch errors={len(fetch_errors)}"
        f" | truncated={truncated}"
    )


if __name__ == "__main__":
    try:
        while True:
            try:
                scan()
            except (requests.RequestException, RuntimeError, ValueError) as exc:
                print(f"Scan failed: {exc}")

            if not RUN_CONTINUOUSLY:
                break

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print("\nStopped.")
