# assistant.py
import os
import sys
import json
import asyncio
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

import google.generativeai as genai # For Gemini

# Import our modules
import config_manager
from mcp_handler import McpSessionManager
import llm_processor # Import our new LLM processor
# import notifier # Next
# import user_interface # Next

# --- Helper for User Input & Signup Flow (remains the same) ---
# ... (get_user_input, get_yes_no_input, run_signup_flow functions are identical to the last version)
def get_user_input(prompt_message: str, default: Optional[str] = None) -> str:
    display_prompt = prompt_message
    if default is not None: display_prompt += f" (default: {default})"
    full_prompt = f"{display_prompt}: "
    while True:
        user_response = input(full_prompt).strip()
        if user_response: return user_response
        elif default is not None: return default
        print("Input cannot be empty. Please try again.")

def get_yes_no_input(prompt_message: str, default_yes: bool = True) -> bool:
    options = "(Y/n)" if default_yes else "(y/N)"
    prompt = f"{prompt_message} {options}: "
    while True:
        response = input(prompt).strip().lower()
        if not response: return default_yes
        if response in ['y', 'yes']: return True
        if response in ['n', 'no']: return False
        print("Invalid input. Please enter 'y' or 'n'.")

def run_signup_flow():
    print("Welcome to your Proactive AI Assistant!")
    print("Let's get you set up.")
    print("-" * 30)
    user_config = {}
    while True:
        email = get_user_input("Please enter your primary email address (this will be your user ID for service connections)")
        if email and "@" in email and "." in email.split("@")[-1]:
            user_config[config_manager.USER_EMAIL_KEY] = email
            break
        else:
            print("Invalid email format. Please try again.")
    user_config[config_manager.USER_PERSONA_KEY] = get_user_input("Describe your role and main work focus")
    user_config[config_manager.USER_PRIORITIES_KEY] = get_user_input("What are your key work priorities?")
    email_notifs_on = get_yes_no_input("Enable notifications for important emails?", default_yes=True)
    calendar_notifs_on = get_yes_no_input("Enable notifications for upcoming calendar events?", default_yes=True)
    user_config[config_manager.NOTIFICATION_PREFS_KEY] = {
        "email": "important" if email_notifs_on else "off",
        "calendar": "on" if calendar_notifs_on else "off"
    }
    gmail_server_uuid = config_manager.DEV_CONFIG.get(config_manager.ENV_GMAIL_MCP_SERVER_UUID)
    calendar_server_uuid = config_manager.DEV_CONFIG.get(config_manager.ENV_CALENDAR_MCP_SERVER_UUID)
    if not gmail_server_uuid or not calendar_server_uuid:
        print("\nError: GMAIL_MCP_SERVER_UUID or CALENDAR_MCP_SERVER_UUID not found in .env file.")
        sys.exit("Critical configuration missing: MCP Server UUIDs.")
    user_config[config_manager.GMAIL_MCP_URL_KEY] = f"https://mcp.composio.dev/composio/server/{gmail_server_uuid}?transport=sse&include_composio_helper_actions=true"
    user_config[config_manager.CALENDAR_MCP_URL_KEY] = f"https://mcp.composio.dev/composio/server/{calendar_server_uuid}?transport=sse&include_composio_helper_actions=true"
    print(f"\nUsing Gmail MCP Server UUID: {gmail_server_uuid}")
    print(f"Using Calendar MCP Server UUID: {calendar_server_uuid}")
    user_config[config_manager.LAST_EMAIL_CHECK_KEY] = datetime.now(timezone.utc).isoformat()
    if config_manager.save_user_config(user_config):
        print("-" * 30)
        print("Setup complete! Your preferences have been saved.")
    else:
        print("Error: Could not save your configuration.")
        sys.exit("Failed to save user configuration.")
    return user_config
# --- Main Application Logic (Async now) ---
async def perform_proactive_checks(user_config, gemini_model) -> bool: # Added gemini_model parameter
    """
    Performs one cycle of proactive checks for Gmail and Calendar.
    Processes fetched data with LLM.
    Returns True if processing completed normally and can continue.
    Returns False if an auth action is required from the user.
    """
    print("\nPerforming proactive checks...")

    user_id = user_config.get(config_manager.USER_EMAIL_KEY)
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a busy professional")
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY, "important tasks and communications")

    gmail_base_url = user_config.get(config_manager.GMAIL_MCP_URL_KEY)
    calendar_base_url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY)

# In assistant.py, within the perform_proactive_checks(user_config, gemini_model) function:

    # --- Gmail Check ---
    all_fetched_raw_messages = [] # Initialize list to store all messages from all pages
    if gmail_base_url and user_id:
        print("\n--- Checking Gmail ---")
        email_cycle_successful_for_timestamp_update = False # For updating LAST_EMAIL_CHECK_KEY
        auth_action_required_for_gmail = False
        try:
            async with McpSessionManager(gmail_base_url, user_id, "gmail") as gmail_manager:
                if not gmail_manager.session:
                     print("Failed to establish Gmail MCP session during __aenter__.")
                else:
                    print(f"Gmail tools available (first 5): {list(gmail_manager.tools.keys())[:5]}...")

                    # --- Define parameters for fetching emails: unread within the last 24 hours ---
                    twenty_four_hours_ago_utc = datetime.now(timezone.utc) - timedelta(hours=24)
                    query_since_timestamp = int(twenty_four_hours_ago_utc.timestamp())

                    gmail_query = f"is:unread after:{query_since_timestamp}"

                    base_fetch_params = {
                        "query": gmail_query,
                        "max_results": 10,        # Max results *per page* from Composio's tool
                        "include_payload": True
                    }

                    current_page_token = None
                    max_pages_to_fetch = 3  # Safety limit: fetch up to e.g., 3 pages of results
                    pages_fetched = 0

                    while pages_fetched < max_pages_to_fetch:
                        pages_fetched += 1
                        current_fetch_params = base_fetch_params.copy()
                        if current_page_token:
                            current_fetch_params["page_token"] = current_page_token

                        print(f"Attempting GMAIL_FETCH_EMAILS (Page {pages_fetched}) with params: {current_fetch_params}")
                        email_result_page = await gmail_manager.ensure_auth_and_call_tool("GMAIL_FETCH_EMAILS", current_fetch_params)

                        if isinstance(email_result_page, dict) and email_result_page.get("needs_user_action"):
                            print("Gmail requires authentication. Please follow instructions and re-run.")
                            auth_action_required_for_gmail = True # Set flag
                            break # Break from pagination loop, will return False from main function
                        elif isinstance(email_result_page, dict) and email_result_page.get("error"):
                            print(f"Error fetching Gmail emails page {pages_fetched}: {email_result_page.get('error')}")
                            current_page_token = None # Stop pagination on error
                            break
                        elif email_result_page and not isinstance(email_result_page, dict) and hasattr(email_result_page, 'content'):
                            if email_result_page.content:
                                for i, item in enumerate(email_result_page.content): # Composio tool returns a list of content items, usually 1
                                    text_content = getattr(item, 'text', None)
                                    if text_content:
                                        try:
                                            email_data_json_page = json.loads(text_content)
                                            if email_data_json_page.get("successful") is True:
                                                messages_on_page = email_data_json_page.get("data", {}).get("messages", [])
                                                if messages_on_page:
                                                    print(f"Found {len(messages_on_page)} email(s) on page {pages_fetched}.")
                                                    all_fetched_raw_messages.extend(messages_on_page)

                                                current_page_token = email_data_json_page.get("data", {}).get("nextPageToken")
                                                if not current_page_token:
                                                    print("No nextPageToken in Gmail response, all emails for query fetched.")
                                                    break # Break from while loop
                                            else: # successful: false from Composio tool
                                                error_from_tool = email_data_json_page.get('error', 'Unknown error from GMAIL_FETCH_EMAILS tool.')
                                                print(f"Composio GMAIL_FETCH_EMAILS reported not successful for page {pages_fetched}: {error_from_tool}")
                                                current_page_token = None
                                                break
                                        except json.JSONDecodeError:
                                            print(f"Could not parse email page {pages_fetched} item {i+1} text as JSON: {text_content[:100]}...")
                                            current_page_token = None
                                            break
                                    else: # No text_content in item
                                        print(f"No text_content in content item from GMAIL_FETCH_EMAILS page {pages_fetched}.")
                                        current_page_token = None # Stop if content structure is unexpected
                                        break
                            else: # email_result_page.content was empty
                                print(f"No content returned from GMAIL_FETCH_EMAILS for page {pages_fetched}.")
                                current_page_token = None
                                break
                        else: # Unknown response structure
                            print(f"Unknown response for GMAIL_FETCH_EMAILS page {pages_fetched}. Result: {email_result_page}")
                            current_page_token = None
                            break

                        if not current_page_token: # Double check to break if it became None inside the loop
                            break
                    # --- End of while loop for pagination ---

                    if not auth_action_required_for_gmail: # If we didn't break for auth
                        email_cycle_successful_for_timestamp_update = True

            if auth_action_required_for_gmail: # If auth was triggered, signal main to exit
                return False

            if email_cycle_successful_for_timestamp_update:
                config_manager.set_last_email_check_timestamp()

        except Exception as e:
            print(f"Outer error during Gmail processing: {e}")
            # traceback.print_exc() # Uncomment for detailed debugging
    else:
        print("Gmail MCP URL or User ID not configured. Skipping Gmail checks.")

# In assistant.py, perform_proactive_checks function

    # --- Process Gmail with LLM ---
    processed_llm_emails = [] # To store LLM's analysis
    important_emails_for_ui = [] # To store only important emails for display/action

    if all_fetched_raw_messages: # Use the aggregated list from pagination
        total_unread_in_24h = len(all_fetched_raw_messages)
        print(f"\n--- Processing {total_unread_in_24h} fetched Gmail messages with LLM ---")

        processed_llm_emails_from_llm = await llm_processor.process_emails_with_llm(
            gemini_model, all_fetched_raw_messages, user_persona, user_priorities
        )

        if processed_llm_emails_from_llm:
            for pe_data in processed_llm_emails_from_llm:
                if pe_data.get('is_important'):
                    important_emails_for_ui.append(pe_data)

            num_important_llm = len(important_emails_for_ui)
            print(f"LLM identified {num_important_llm} important email(s) out of {total_unread_in_24h} processed.")

            # For now, let's print details of important ones
            if important_emails_for_ui:
                            print("\nDetails of Important Emails:")
                            for idx, imp_email_data_from_llm in enumerate(important_emails_for_ui):
                                print(f"Important Email #{idx+1}:")

                                original_email_obj = imp_email_data_from_llm.get("original_email_data", {})

                                # Try Composio's top-level extracted fields first
                                sender = original_email_obj.get("sender", "Unknown Sender (from top-level)")
                                subject = original_email_obj.get("subject", "No Subject (from top-level)")

                                # Fallback to parsing headers if top-level fields were default or you want to be sure
                                # (though if Composio provides them, they should be accurate)
                                if sender == "Unknown Sender (from top-level)" or subject == "No Subject (from top-level)":
                                    payload = original_email_obj.get("payload")
                                    if payload and isinstance(payload.get("headers"), list):
                                        for header in payload["headers"]:
                                            header_name_lower = header.get("name", "").lower()
                                            if header_name_lower == "from":
                                                sender = header.get("value", "Unknown Sender (from headers)")
                                            elif header_name_lower == "subject":
                                                subject = header.get("value", "No Subject (from headers)")
                                    else: # If no payload or headers, stick with initial defaults or top-level values if they existed
                                        if sender == "Unknown Sender (from top-level)": sender = "Unknown Sender"
                                        if subject == "No Subject (from top-level)": subject = "No Subject"


                                print(f"  From: {sender}")
                                print(f"  Subject: {subject}")
                                print(f"  LLM Summary: {imp_email_data_from_llm.get('summary')}")
                                print(f"  LLM Suggested Actions: {imp_email_data_from_llm.get('suggested_actions')}")
                                print("-" * 10)
            # else:
            #     print("No emails were deemed important by the LLM.")
        else:
            print("LLM processing for emails did not return any items (list was empty or None).")
            # This implies an issue with llm_processor or the LLM call itself if raw messages were present
    else:
        print("No raw Gmail messages to process with LLM (either none found after pagination or an error occurred before fetch).")

    # TODO: Pass important_emails_for_ui and processed_events to notifier and user_interface
    # For notifier:
    # num_important_emails_count = len(important_emails_for_ui)
    # num_upcoming_events_count = len(raw_calendar_events) # Or len(processed_events from LLM)
    # notification_message = f"{num_important_emails_count} important email(s). {num_upcoming_events_count} upcoming event(s)."
    # await notifier.send_macos_notification("Proactive Assistant", notification_message)

    # For user_interface:
    # await user_interface.display_and_handle_actions(important_emails_for_ui, processed_events_from_llm, user_config, gemini_model, gmail_mcp_manager_context, calendar_mcp_manager_context)
    # The McpSessionManagers would need to be passed or re-established for actions.

    # --- Calendar Check ---
    raw_calendar_events = [] # To store raw events for LLM
    if calendar_base_url and user_id:
        print("\n--- Checking Calendar ---")
        try:
            async with McpSessionManager(calendar_base_url, user_id, "googlecalendar") as calendar_manager:
                if not calendar_manager.session:
                    print("Failed to establish Calendar MCP session.")
                else:
                    print(f"Calendar tools available (first 5): {list(calendar_manager.tools.keys())[:5]}...")
                    now_utc = datetime.now(timezone.utc)
                    time_min_str = now_utc.isoformat().replace("+00:00", "Z")
                    time_max_str = (now_utc + timedelta(days=1)).isoformat().replace("+00:00", "Z")
                    calendar_fetch_params = {
                        "calendarId": "primary", "timeMin": time_min_str,
                        "timeMax": time_max_str, "maxResults": 10, # Get more events for LLM
                        "singleEvents": True, "orderBy": "startTime"
                    }
                    print(f"Attempting to call GOOGLECALENDAR_FIND_EVENT with params: {calendar_fetch_params}")
                    event_result = await calendar_manager.ensure_auth_and_call_tool("GOOGLECALENDAR_FIND_EVENT", calendar_fetch_params)

                    if isinstance(event_result, dict) and event_result.get("needs_user_action"):
                        print("Google Calendar requires authentication. Please follow instructions and re-run.")
                        return False
                    elif isinstance(event_result, dict) and event_result.get("error"):
                        print(f"Error fetching Calendar events: {event_result.get('error')}")
                    elif event_result and not isinstance(event_result, dict) and hasattr(event_result, 'content'):
                        print("Successfully fetched Calendar events.")
                        if event_result.content:
                            for i, item in enumerate(event_result.content):
                                text_content = getattr(item, 'text', None)
                                if text_content:
                                    try:
                                        event_data_json = json.loads(text_content)
                                        actual_events = event_data_json.get("data",{}).get("event_data",{}).get("event_data",[])
                                        if actual_events:
                                            print(f"Found {len(actual_events)} raw event(s) in content item {i+1}.")
                                            raw_calendar_events.extend(actual_events) # Add to list for LLM
                                        # else:
                                            # print(f"No events in structured response for item {i+1} (Calendar).")
                                    except json.JSONDecodeError:
                                        print(f"Could not parse calendar item {i+1} text as JSON: {text_content[:100]}...")
                        # else: # This means event_result.content was empty
                            # print("No calendar events found for the period (content was empty).")
                    else:
                        print(f"Unknown response or no events found for Calendar. Result: {event_result}")
        except Exception as e:
            print(f"Error during Calendar processing: {e}")
            # traceback.print_exc()
    else:
        print("Calendar MCP URL or User ID not configured. Skipping Calendar checks.")

    # --- Process Calendar with LLM ---
    if raw_calendar_events:
        print("\n--- Processing Calendar with LLM ---")
        processed_events = await llm_processor.process_calendar_events_with_llm(
            gemini_model, raw_calendar_events, user_persona, user_priorities
        )
        print("LLM Processed Calendar Events:")
        for pc_idx, pc_data in enumerate(processed_events):
            print(f"Processed Event #{pc_idx+1}:")
            print(f"  LLM Summary: {pc_data.get('summary_llm')}")
            print(f"  Suggested Actions: {pc_data.get('suggested_actions')}")
            # print(f"  Original ID: {pc_data.get('original_event_data', {}).get('id')}") # For debug
            print("-" * 10)
        # TODO: Store/use processed_events for UI and quick actions
    else:
        print("No raw Calendar events to process with LLM.")


    print("\nProactive checks cycle complete.")
    return True


async def main_assistant_entry():
    """Entry point for the assistant logic."""
    if not config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY):
        print(f"Error: {config_manager.ENV_GOOGLE_API_KEY} is not set in .env file.")
        sys.exit(f"Critical configuration missing: {config_manager.ENV_GOOGLE_API_KEY}")

    user_configuration = config_manager.load_user_config()

    if not user_configuration or not user_configuration.get(config_manager.USER_EMAIL_KEY):
        print("Running first-time setup...")
        user_configuration = run_signup_flow()
        if not (user_configuration and user_configuration.get(config_manager.USER_EMAIL_KEY)):
            print("Signup was not completed successfully. Exiting.")
            return
        print("\nInitial setup complete. The assistant will now perform its first check.")
        # It's good practice to re-load after saving to ensure consistency, though run_signup_flow returns it.
        user_configuration = config_manager.load_user_config()


    print(f"Welcome back, {user_configuration.get(config_manager.USER_EMAIL_KEY)}!")
    print("Proactive Assistant starting its check...")

    # Initialize Gemini Model
    google_api_key = config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY)
    try:
        genai.configure(api_key=google_api_key)
        gemini_llm_model = genai.GenerativeModel('gemini-2.5-flash-preview-05-20') # Or your preferred model
        print("Gemini model initialized successfully.")
    except Exception as e:
        print(f"Failed to initialize Gemini model: {e}")
        traceback.print_exc()
        sys.exit("Could not initialize LLM. Exiting.")

    should_continue = await perform_proactive_checks(user_configuration, gemini_llm_model)

    if not should_continue:
        print("Exiting assistant due to required user action (e.g., authentication). Please re-run after completing the action.")
        return

    print("\nAssistant check finished for this cycle.")
    # TODO: Implement scheduling loop (Phase 7) and interactive quick actions.

if __name__ == "__main__":
    try:
        asyncio.run(main_assistant_entry())
    except KeyboardInterrupt:
        print("\nAssistant stopped by user. Goodbye!")
    except SystemExit: # Catch sys.exit() to allow clean exit
        print("Assistant is exiting as requested.")
    except Exception as e:
        print(f"An unexpected error occurred in the main execution: {e}")
        traceback.print_exc()
