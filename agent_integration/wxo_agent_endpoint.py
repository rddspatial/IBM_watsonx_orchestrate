import requests, time, json


REGION = ""  # e.g., us-south, eu-de
INSTANCE_ID = ""  # <-- your wxo instance id/URL
AGENT_ID = ""     # <-- your agent id
API_KEY = '' # YOUR IBM CLOUD API KEY

IAM_URL = "https://iam.cloud.ibm.com/identity/token"
BASE_URL = f"https://api.{REGION}.watson-orchestrate.cloud.ibm.com/instances/{INSTANCE_ID}"

def get_iam_token(api_key: str) -> str:
    r = requests.post(
        IAM_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type":"urn:ibm:params:oauth:grant-type:apikey","apikey":api_key},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["access_token"]

def start_run(token: str, agent_id: str, user_text: str) -> str:
    url = f"{BASE_URL}/v1/orchestrate/runs"
    payload = {
        "message": {"role": "user", "content": user_text},
        "agent_id": agent_id
    }
    r = requests.post(
        url, json=payload,
        headers={"Authorization": f"Bearer {token}", "Accept":"application/json"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["run_id"]

def wait_for_run(token: str, run_id: str, poll_sec: float = 0.5, max_wait: float = 120.0) -> dict:
    url = f"{BASE_URL}/v1/orchestrate/runs/{run_id}"
    waited = 0.0
    while True:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}", "Accept":"application/json"}, timeout=60)
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status not in ("pending", "running"):
            return data
        time.sleep(poll_sec)
        waited += poll_sec
        if waited >= max_wait:
            raise TimeoutError(f"Run {run_id} not finished after {max_wait}s")

def get_thread_messages(token: str, thread_id: str) -> list:
    url = f"{BASE_URL}/v1/orchestrate/threads/{thread_id}/messages"
    r = requests.get(url, headers={"Authorization": f"Bearer {token}", "Accept":"application/json"}, timeout=60)
    r.raise_for_status()
    return r.json()

if __name__ == "__main__":
    token = get_iam_token(API_KEY)
    print("Got IAM token")

    run_id = start_run(token, AGENT_ID, "Hello, are you alive?") # user query
    print("Started run:", run_id)

    run_info = wait_for_run(token, run_id)
  #  print("Run finished:", json.dumps(run_info, indent=2))

    thread_id = run_info["thread_id"]
    msgs = get_thread_messages(token, thread_id)
    print("Messages:", json.dumps(msgs, indent=2))

    # Extract the assistant reply (simple pass)
    answer = None
    for m in msgs:
        if m.get("role") == "assistant" and m.get("content"):
            # content can be a list of blocks; handle text blocks
            blocks = m["content"]
            if isinstance(blocks, list) and blocks and "text" in blocks[0]:
                answer = blocks[0]["text"]
                break
    print("Agent says:", answer or "(no assistant message found)")
