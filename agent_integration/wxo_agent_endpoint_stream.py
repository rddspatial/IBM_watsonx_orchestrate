import json
import time



# ============================================================
# CONFIGURATION
# ============================================================


IBM_CLOUD_API_KEY = '<YOUR IBM CLOUD API KEY>'


# ------------------------------------------------------------
# watsonx Orchestrate configuration
# ------------------------------------------------------------

# Copy from:
# watsonx Orchestrate -> Settings -> API details
#

WXO_SERVICE_URL = '<YOUR WXO URL>'

# Your watsonx Orchestrate agent UUID
WXO_AGENT_ID = '<YOUR WXO AGENT ID>'


# ------------------------------------------------------------
# Debug mode
#
# False = only show the normal streamed answer
# True  = show raw WXO streaming events
# ------------------------------------------------------------

DEBUG_STREAM = False


# ============================================================
# IAM TOKEN CACHE
# ============================================================

iam_token = None
iam_token_expiry = 0


# ============================================================
# 1. GET IBM CLOUD IAM TOKEN
# ============================================================

def get_iam_token():

    global iam_token
    global iam_token_expiry

    # --------------------------------------------------------
    # Reuse existing token if it is still valid
    # Refresh 60 seconds before expiration
    # --------------------------------------------------------

    if (
        iam_token is not None
        and time.time() < iam_token_expiry - 60
    ):
        return iam_token


    # --------------------------------------------------------
    # Request a new IAM token
    # --------------------------------------------------------

    url = "https://iam.cloud.ibm.com/identity/token"

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    data = {
        "grant_type":
            "urn:ibm:params:oauth:grant-type:apikey",

        "apikey":
            IBM_CLOUD_API_KEY,
    }

    response = requests.post(
        url,
        headers=headers,
        data=data,
        timeout=30,
    )

    response.raise_for_status()

    token_data = response.json()


    # --------------------------------------------------------
    # Cache token
    # --------------------------------------------------------

    iam_token = token_data["access_token"]


    # --------------------------------------------------------
    # IBM IAM normally returns expiration timestamp
    # --------------------------------------------------------

    if "expiration" in token_data:

        iam_token_expiry = token_data["expiration"]

    else:

        # Fallback
        iam_token_expiry = time.time() + 3600


    return iam_token


# ============================================================
# 2. CALL WATSONX ORCHESTRATE STREAMING ENDPOINT
# ============================================================

def call_agent_stream(
    message,
    thread_id=None
):

    # --------------------------------------------------------
    # Get/reuse IAM token
    # --------------------------------------------------------

    token = get_iam_token()


    # --------------------------------------------------------
    # WXO streaming endpoint
    # --------------------------------------------------------

    url = (
        f"{WXO_SERVICE_URL.rstrip('/')}"
        "/v1/orchestrate/runs/stream"
    )


    # --------------------------------------------------------
    # HTTP headers
    # --------------------------------------------------------

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


    # --------------------------------------------------------
    # Request payload
    # --------------------------------------------------------

    payload = {

        "message": {
            "role": "user",
            "content": message
        },

        "agent_id": WXO_AGENT_ID
    }


    # --------------------------------------------------------
    # Continue an existing WXO conversation
    # --------------------------------------------------------

    if thread_id:

        payload["thread_id"] = thread_id


    # --------------------------------------------------------
    # Call watsonx Orchestrate
    # --------------------------------------------------------

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        stream=True,
        timeout=300
    )


    # --------------------------------------------------------
    # HTTP error handling
    # --------------------------------------------------------

    if not response.ok:

        print("\nHTTP STATUS:")
        print(response.status_code)

        print("\nRESPONSE:")
        print(response.text)


    response.raise_for_status()


    # --------------------------------------------------------
    # Current/new conversation thread
    # --------------------------------------------------------

    new_thread_id = thread_id


    # ========================================================
    # PROCESS WXO SERVER-SENT EVENT STREAM
    # ========================================================

    for line in response.iter_lines(
        decode_unicode=True
    ):

        # ----------------------------------------------------
        # Ignore empty SSE lines
        # ----------------------------------------------------

        if not line:
            continue


        # ----------------------------------------------------
        # SSE payload:
        #
        # data: {...}
        # ----------------------------------------------------

        if line.startswith("data:"):

            line = line[5:].strip()


        # ----------------------------------------------------
        # Ignore SSE metadata
        # ----------------------------------------------------

        elif line.startswith("event:"):

            continue


        elif line.startswith("id:"):

            continue


        # ----------------------------------------------------
        # Stream finished
        # ----------------------------------------------------

        if line == "[DONE]":

            break


        # ----------------------------------------------------
        # Parse JSON event
        # ----------------------------------------------------

        try:

            event = json.loads(line)

        except json.JSONDecodeError:

            if DEBUG_STREAM:

                print("\nNON-JSON STREAM DATA:")
                print(line)

            continue


        # ====================================================
        # DEBUG MODE
        # ====================================================

        if DEBUG_STREAM:

            print()
            print()
            print("--- WXO EVENT ---")

            print(
                json.dumps(
                    event,
                    indent=2
                )
            )


        # ====================================================
        # GET EVENT TYPE AND DATA
        # ====================================================

        event_type = event.get("event")

        data = event.get(
            "data",
            {}
        )


        # ====================================================
        # CAPTURE THREAD ID
        # ====================================================

        if isinstance(data, dict):

            possible_thread_id = (
                data.get("thread_id")
                or event.get("thread_id")
            )

            if possible_thread_id:

                new_thread_id = possible_thread_id


        # ====================================================
        # MESSAGE DELTA
        #
        # Actual WXO structure observed:
        #
        # {
        #   "event": "message.delta",
        #   "data": {
        #
        #       "thread_id": "...",
        #
        #       "delta": {
        #
        #           "role": "assistant",
        #
        #           "content": [
        #               {
        #                   "id": "1",
        #                   "response_type": "text",
        #                   "text": "We"
        #               }
        #           ]
        #       }
        #   }
        # }
        #
        # ====================================================

        if event_type == "message.delta":

            # ------------------------------------------------
            # data -> delta
            # ------------------------------------------------

            delta = data.get(
                "delta",
                {}
            )

            if not isinstance(
                delta,
                dict
            ):
                continue


            # ------------------------------------------------
            # data -> delta -> content
            # ------------------------------------------------

            content = delta.get(
                "content",
                []
            )

            if not isinstance(
                content,
                list
            ):
                continue


            # ------------------------------------------------
            # Process each streamed response item
            # ------------------------------------------------

            for item in content:

                if not isinstance(
                    item,
                    dict
                ):
                    continue


                # --------------------------------------------
                # We only want normal text
                # --------------------------------------------

                if (
                    item.get("response_type")
                    == "text"
                ):

                    text = item.get(
                        "text",
                        ""
                    )


                    # ----------------------------------------
                    # Print immediately
                    #
                    # end="" joins chunks
                    # flush=True displays immediately
                    # ----------------------------------------

                    if text:

                        print(
                            text,
                            end="",
                            flush=True
                        )


        # ====================================================
        # MESSAGE CREATED
        #
        # WXO also sends the complete assembled message here.
        #
        # We DON'T print it because we've already streamed
        # the individual message.delta chunks.
        # ====================================================

        elif event_type == "message.created":

            if DEBUG_STREAM:

                print(
                    "\n[MESSAGE CREATED]"
                )


        # ====================================================
        # MESSAGE COMPLETED
        # ====================================================

        elif event_type == "message.completed":

            if DEBUG_STREAM:

                print(
                    "\n[MESSAGE COMPLETED]"
                )


        # ====================================================
        # RUN COMPLETED
        # ====================================================

        elif event_type == "run.completed":

            if DEBUG_STREAM:

                print(
                    "\n[RUN COMPLETED]"
                )

            break


        # ====================================================
        # RUN FAILED
        # ====================================================

        elif event_type == "run.failed":

            print()
            print()
            print(
                "Agent run failed."
            )

            print(
                json.dumps(
                    event,
                    indent=2
                )
            )

            break


        # ====================================================
        # ERROR EVENT
        # ====================================================

        elif event_type == "error":

            print()
            print()
            print(
                "watsonx Orchestrate error."
            )

            print(
                json.dumps(
                    event,
                    indent=2
                )
            )

            break


    # --------------------------------------------------------
    # New line after streaming response
    # --------------------------------------------------------

    print()


    # --------------------------------------------------------
    # Return thread ID so next question continues conversation
    # --------------------------------------------------------

    return new_thread_id


# ============================================================
# 3. INTERACTIVE TERMINAL CHAT
# ============================================================

def start_chat():

    print()
    print("=" * 65)
    print("Maritime Domain Awareness Agent")
    print("Powered by IBM watsonx Orchestrate")
    print("=" * 65)

    print()
    print("Commands:")
    print("  new   - Start a new conversation")
    print("  exit  - Exit")
    print("  quit  - Exit")
    print()


    # --------------------------------------------------------
    # Current conversation thread
    # --------------------------------------------------------

    thread_id = None


    # ========================================================
    # INTERACTIVE CHAT LOOP
    # ========================================================

    while True:

        try:

            # ------------------------------------------------
            # User input
            # ------------------------------------------------

            user_message = input(
                "\nYou: "
            ).strip()


            # ------------------------------------------------
            # Ignore empty input
            # ------------------------------------------------

            if not user_message:

                continue


            # ------------------------------------------------
            # Exit
            # ------------------------------------------------

            if user_message.lower() in [
                "exit",
                "quit"
            ]:

                print()
                print("Goodbye!")
                print()

                break


            # ------------------------------------------------
            # Start a completely new WXO conversation
            # ------------------------------------------------

            if user_message.lower() == "new":

                thread_id = None

                print()
                print(
                    "Started a new conversation."
                )

                continue


            # ------------------------------------------------
            # Agent label
            # ------------------------------------------------

            print(
                "\nAgent: ",
                end="",
                flush=True
            )


            # ------------------------------------------------
            # Stream response
            # ------------------------------------------------

            thread_id = call_agent_stream(
                message=user_message,
                thread_id=thread_id
            )


            # ------------------------------------------------
            # Optional debugging
            # ------------------------------------------------

            if DEBUG_STREAM:

                print()
                print(
                    f"THREAD ID: {thread_id}"
                )


        # ====================================================
        # CTRL + C
        # ====================================================

        except KeyboardInterrupt:

            print()
            print()
            print("Chat stopped.")
            print()

            break


        # ====================================================
        # NETWORK / API ERROR
        # ====================================================

        except requests.exceptions.RequestException as e:

            print()
            print()
            print(
                "Network/API Error:"
            )

            print(e)


        # ====================================================
        # OTHER ERROR
        # ====================================================

        except Exception as e:

            print()
            print()
            print(
                "Unexpected Error:"
            )

            print(e)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    start_chat()
