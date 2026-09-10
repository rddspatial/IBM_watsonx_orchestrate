import os
import requests

# ============================================================
# CONFIGURATION
# ============================================================

IBM_CLOUD_API_KEY = '<YOUR IBM CLOUD API KEY>'

# Copy this directly from:
# watsonx Orchestrate -> Settings -> API details -> Service instance URL
#
# Example:
# https://api.eu-de.watson.cloud.ibm.com/instances/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
#
WXO_SERVICE_URL = '<YOUR WXO URL>'

# Your watsonx Orchestrate agent UUID
WXO_AGENT_ID = '<YOUR WXO AGENT ID>'


# ============================================================
# 1. GET IBM CLOUD IAM TOKEN
# ============================================================

def get_iam_token():
    url = "https://iam.cloud.ibm.com/identity/token"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    data = {
        "grant_type": "urn:ibm:params:oauth:grant-type:apikey",
        "apikey": IBM_CLOUD_API_KEY,
    }

    response = requests.post(
        url,
        headers=headers,
        data=data,
        timeout=30,
    )

    response.raise_for_status()

    return response.json()["access_token"]


# ============================================================
# 2. CALL WATSONX ORCHESTRATE AGENT
# ============================================================

def call_agent(message, thread_id=None):

    token = get_iam_token()

    url = (
        f"{WXO_SERVICE_URL.rstrip('/')}"
        f"/v1/orchestrate/{WXO_AGENT_ID}/chat/completions"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # If this is a continuation of an existing conversation,
    # send the previous thread ID.
    if thread_id:
        headers["X-IBM-THREAD-ID"] = thread_id

    payload = {
        "messages": [
            {
                "role": "user",
                "content": message
            }
        ],
        "stream": False
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=120,
    )

    if not response.ok:
        print("HTTP STATUS:", response.status_code)
        print("RESPONSE:", response.text)

    response.raise_for_status()

    return response.json()


# ============================================================
# TEST
# ============================================================

# ============================================================
# INTERACTIVE TERMINAL CHAT
# ============================================================

if __name__ == "__main__":

    print("=" * 60)
    print("watsonx Orchestrate Agent")
    print("Type 'exit' or 'quit' to stop")
    print("=" * 60)

    thread_id = None

    while True:

        # Get user input from terminal
        user_message = input("\nYou: ").strip()

        # Ignore empty messages
        if not user_message:
            continue

        # Exit commands
        if user_message.lower() in ["exit", "quit"]:
            print("\nGoodbye!")
            break

        try:
            # Call WXO agent
            response = call_agent(
                user_message,
                thread_id=thread_id
            )

            # Extract answer
            answer = response["choices"][0]["message"]["content"]

            # Save/update thread ID
            thread_id = response.get("thread_id")

            # Print only agent response
            print(f"\nAgent: {answer}")

        except requests.exceptions.RequestException as e:
            print(f"\nError calling watsonx Orchestrate: {e}")

        except (KeyError, IndexError) as e:
            print("\nUnexpected response from agent:")
            print(response)
