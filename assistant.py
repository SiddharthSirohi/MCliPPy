import os
import sys
import json
import asyncio
import traceback
import platform
import argparse
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
import google.generativeai as genai

# Import our modules
import config_manager
from mcp_handler import McpSessionManager
import llm_processor
import notifier
import user_interface
import calendar_utils


# --- Helper for User Input & Signup Flow (from user_interface.py now) ---
# These are called from user_interface.py, so no need to redefine here.

# assistant.py
def generate_launchd_plist_content(
    script_path: str,
    working_directory: str,
    label_prefix: str,
    frequency_minutes: int,
    log_dir: str
) -> str:
    print(f"DEBUG_PLIST_GEN (Start of function): Received working_directory: {working_directory}")
    project_root = Path(working_directory).resolve()
    print(f"DEBUG_PLIST_GEN: Resolved project_root: {project_root}")

    venv_python_path = project_root / ".venv" / "bin" / "python"
    print(f"DEBUG_PLIST_GEN: Expected venv_python_path: {venv_python_path}")


    # Determine which python executable to use
    path_exists_check = venv_python_path.exists() # Store the result of exists()
    print(f"DEBUG_PLIST_GEN: Result of venv_python_path.exists(): {path_exists_check}") # DEBUG

    if path_exists_check: # Use the stored result
        print(f"{user_interface.Fore.GREEN}DEBUG_PLIST_GEN: venv_python_path EXISTS branch taken.{user_interface.Style.RESET_ALL}")
        python_exec_to_use_candidate = str(venv_python_path)
        print(f"DEBUG_PLIST_GEN: Candidate from venv: {python_exec_to_use_candidate}")
        python_exec_to_use = python_exec_to_use_candidate # Direct assignment
    else:
        print(f"{user_interface.Fore.RED}DEBUG_PLIST_GEN: venv_python_path DOES NOT EXIST branch taken.{user_interface.Style.RESET_ALL}")
        current_sys_executable = str(Path(sys.executable).resolve())
        print(f"{user_interface.Fore.YELLOW}WARNING: Virtual environment Python not found. Falling back to sys.executable: {current_sys_executable}{user_interface.Style.RESET_ALL}")
        python_exec_to_use = current_sys_executable

    print(f"DEBUG_PLIST_GEN: VALUE OF 'python_exec_to_use' AFTER IF/ELSE: {python_exec_to_use}") # Renamed for clarity

    # **** ADD THIS FINAL OVERRIDE FOR DEBUGGING ****
    # **** If the above still fails, this will force it for the plist string ****
    # python_exec_to_use = str(venv_python_path.resolve())
    # print(f"DEBUG_PLIST_GEN: FORCED VALUE of 'python_exec_to_use' FOR PLIST: {python_exec_to_use}")
    # **** END OF DEBUGGING OVERRIDE ****

    script_path_resolved = str(Path(script_path).resolve())
    log_dir_resolved = str(Path(log_dir).resolve())
    Path(log_dir_resolved).mkdir(parents=True, exist_ok=True)
    label = f"{label_prefix}.proactiveassistant"
    interval_seconds = int(frequency_minutes * 60)

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_exec_to_use}</string>
        <string>{script_path_resolved}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{str(project_root)}</string>
    <key>StartInterval</key>
    <integer>{interval_seconds}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_dir_resolved}/assistant_out.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir_resolved}/assistant_err.log</string>
</dict>
</plist>
"""
    return plist_content

def run_signup_flow(): # Stays in assistant.py as it uses config_manager directly
    print(f"{user_interface.Fore.CYAN}Welcome to your Proactive AI Assistant!{user_interface.Style.RESET_ALL}")
    print("Let's get you set up.")
    user_interface.print_header("Initial Setup") # Using new UI helper

    user_config = {}
    while True:
        email = user_interface.get_user_input("Please enter your primary email address (this will be your user ID for service connections)")
        if email and "@" in email and "." in email.split("@")[-1]:
            user_config[config_manager.USER_EMAIL_KEY] = email
            break
        else:
            print(f"{user_interface.Fore.RED}Invalid email format. Please try again.{user_interface.Style.RESET_ALL}")

    user_config[config_manager.USER_PERSONA_KEY] = user_interface.get_user_input("Describe your role and main work focus")
    user_config[config_manager.USER_PRIORITIES_KEY] = user_interface.get_user_input("What are your key work priorities?")

    email_notifs_on = user_interface.get_yes_no_input("Enable notifications for important emails?", default_yes=True)
    calendar_notifs_on = user_interface.get_yes_no_input("Enable notifications for upcoming calendar events?", default_yes=True)

    user_config[config_manager.NOTIFICATION_PREFS_KEY] = {
        "email": "important" if email_notifs_on else "off",
        "calendar": "on" if calendar_notifs_on else "off"
    }

    user_interface.print_header("Scheduling Preferences")
    # --- ADD SCHEDULING AND WORKING HOURS PROMPTS ---
    while True:
        try:
            freq_str = user_interface.get_user_input("How often should the assistant check for updates (e.g., 15m, 30m, 1h)?", default="30m")
            if 'h' in freq_str:
                user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY] = int(freq_str.replace('h', '')) * 60
            elif 'm' in freq_str:
                user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY] = int(freq_str.replace('m', ''))
            else:
                user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY] = int(freq_str) # Assume minutes if no unit
            if user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY] > 0:
                break
            else:
                print(f"{user_interface.Fore.RED}Frequency must be positive.{user_interface.Style.RESET_ALL}")
        except ValueError:
            print(f"{user_interface.Fore.RED}Invalid frequency format. Use numbers optionally followed by 'm' or 'h'.{user_interface.Style.RESET_ALL}")

    days_map = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
    active_days_input_str = user_interface.get_user_input(
        "On which days should the assistant be active? (e.g., mon,tue,wed,thu,fri or 'all' or 'weekdays')",
        default="weekdays"
    ).lower()
    selected_days = []
    if "all" in active_days_input_str:
        selected_days = list(range(7))
    elif "weekdays" in active_days_input_str:
        selected_days = [0, 1, 2, 3, 4]
    else:
        for day_abbr in active_days_input_str.split(','):
            if day_abbr.strip() in days_map:
                selected_days.append(days_map[day_abbr.strip()])
    user_config[config_manager.SCHED_ACTIVE_DAYS_KEY] = sorted(list(set(selected_days))) # Remove duplicates and sort

    while True:
        try:
            start_h_str = user_interface.get_user_input("What hour should checks START (0-23, e.g., 9 for 9 AM)?", default="9")
            start_h = int(start_h_str)
            if 0 <= start_h <= 23:
                user_config[config_manager.SCHED_ACTIVE_START_HOUR_KEY] = start_h
                user_config[config_manager.WORK_START_HOUR_KEY] = start_h # Default working hour start to schedule start
                break
            else:
                print(f"{user_interface.Fore.RED}Hour must be 0-23.{user_interface.Style.RESET_ALL}")
        except ValueError:
            print(f"{user_interface.Fore.RED}Invalid hour.{user_interface.Style.RESET_ALL}")

    while True:
        try:
            end_h_str = user_interface.get_user_input(f"What hour should checks END (0-23, e.g., 18 for up to 6 PM, must be after start hour {user_config[config_manager.SCHED_ACTIVE_START_HOUR_KEY]})?", default="18")
            end_h = int(end_h_str)
            if user_config[config_manager.SCHED_ACTIVE_START_HOUR_KEY] < end_h <= 23: # Can end at 23 (up to 11:59 PM)
                user_config[config_manager.SCHED_ACTIVE_END_HOUR_KEY] = end_h
                user_config[config_manager.WORK_END_HOUR_KEY] = end_h # Default working hour end to schedule end
                break
            else:
                print(f"{user_interface.Fore.RED}End hour must be after start hour and <= 23.{user_interface.Style.RESET_ALL}")
        except ValueError:
            print(f"{user_interface.Fore.RED}Invalid hour.{user_interface.Style.RESET_ALL}")

    # Optionally, ask specifically for working hours if they differ from active check hours
    if user_interface.get_yes_no_input("Are your typical working hours for free-slot calculation different from these active check hours?", default_yes=False):
        while True: # Working Start Hour
            try:
                work_start_h_str = user_interface.get_user_input("Your typical workday START hour (0-23)?", default=str(user_config[config_manager.SCHED_ACTIVE_START_HOUR_KEY]))
                work_start_h = int(work_start_h_str)
                if 0 <= work_start_h <= 23:
                    user_config[config_manager.WORK_START_HOUR_KEY] = work_start_h
                    break
            except ValueError: print(f"{user_interface.Fore.RED}Invalid hour.{user_interface.Style.RESET_ALL}")
        while True: # Working End Hour
            try:
                work_end_h_str = user_interface.get_user_input(f"Your typical workday END hour (0-23, after {user_config[config_manager.WORK_START_HOUR_KEY]})?", default=str(user_config[config_manager.SCHED_ACTIVE_END_HOUR_KEY]))
                work_end_h = int(work_end_h_str)
                if user_config[config_manager.WORK_START_HOUR_KEY] < work_end_h <= 23:
                    user_config[config_manager.WORK_END_HOUR_KEY] = work_end_h
                    break
            except ValueError: print(f"{user_interface.Fore.RED}Invalid hour.{user_interface.Style.RESET_ALL}")
    # --- END OF SCHEDULING AND WORKING HOURS PROMPTS ---


    gmail_server_uuid = config_manager.DEV_CONFIG.get(config_manager.ENV_GMAIL_MCP_SERVER_UUID)
    calendar_server_uuid = config_manager.DEV_CONFIG.get(config_manager.ENV_CALENDAR_MCP_SERVER_UUID)

    if not gmail_server_uuid or not calendar_server_uuid:
        print(f"\n{user_interface.Fore.RED}Error: GMAIL_MCP_SERVER_UUID or CALENDAR_MCP_SERVER_UUID not found in .env file.{user_interface.Style.RESET_ALL}")
        sys.exit("Critical configuration missing: MCP Server UUIDs.")

    user_config[config_manager.GMAIL_MCP_URL_KEY] = f"https://mcp.composio.dev/composio/server/{gmail_server_uuid}?transport=sse&include_composio_helper_actions=true"
    user_config[config_manager.CALENDAR_MCP_URL_KEY] = f"https://mcp.composio.dev/composio/server/{calendar_server_uuid}?transport=sse&include_composio_helper_actions=true"

    print(f"\nUsing Gmail MCP Server UUID: {user_interface.Fore.YELLOW}{gmail_server_uuid}{user_interface.Style.RESET_ALL}")
    print(f"Using Calendar MCP Server UUID: {user_interface.Fore.YELLOW}{calendar_server_uuid}{user_interface.Style.RESET_ALL}")

    user_config[config_manager.LAST_EMAIL_CHECK_KEY] = datetime.now(timezone.utc).isoformat()

    if config_manager.save_user_config(user_config):
        user_interface.print_header("Setup Complete & launchd Agent Configuration")
        print(f"{user_interface.Fore.GREEN}Your preferences have been saved.{user_interface.Style.RESET_ALL}")

        # +++++++++++++ SART OF NEW .PLIST GENERATION LOGIC +++++++++++++
        if platform.system() == "Darwin":
            try:
                script_file_path = Path(__file__).resolve()
                work_dir_for_plist = script_file_path.parent # This is the project root

                print(f"DEBUG_SIGNUP: Path(__file__).resolve() (script_file_path): {script_file_path}") # DEBUG
                print(f"DEBUG_SIGNUP: work_dir_for_plist (passed as working_directory): {work_dir_for_plist}") # DEBUG

                log_storage_dir = config_manager.CONFIG_DIR_PATH
                user_name = os.getenv("USER", "defaultuser")
                label_prefix_str = f"com.{user_name}"

                plist_content_str = generate_launchd_plist_content(
                    script_path=str(script_file_path),
                    working_directory=str(work_dir_for_plist), # This IS project_root
                    label_prefix=label_prefix_str,
                    frequency_minutes=user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY],
                    log_dir=str(log_storage_dir)
                )

                launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
                launch_agents_dir.mkdir(parents=True, exist_ok=True)

                plist_filename = f"{label_prefix_str}.proactiveassistant.plist"
                plist_file_path = launch_agents_dir / plist_filename

                with open(plist_file_path, "w") as f:
                    f.write(plist_content_str)


                print(f"\n{user_interface.Fore.GREEN}A launchd agent file has been created at:{user_interface.Style.RESET_ALL}")
                print(f"  {plist_file_path}")
                print(f"\n{user_interface.Fore.YELLOW}To enable automatic background checks, open Terminal and run:{user_interface.Style.RESET_ALL}")
                print(f"  launchctl load {plist_file_path}")
                print(f"\n{user_interface.Fore.CYAN}The assistant will then run every {user_config[config_manager.SCHED_FREQUENCY_MINUTES_KEY]} minutes during your active hours/days.{user_interface.Style.RESET_ALL}")
                print(f"Logs will be written to: {log_storage_dir}/assistant_out.log and assistant_err.log")
                print(f"To stop automatic checks, run:")
                print(f"  launchctl unload {plist_file_path}")

            except Exception as e:
                print(f"\n{user_interface.Fore.RED}Error creating launchd agent file: {e}{user_interface.Style.RESET_ALL}")
                print(f"{user_interface.Fore.YELLOW}You will need to set up scheduling manually if desired.{user_interface.Style.RESET_ALL}")
    else:
        print(f"{user_interface.Fore.RED}Error: Could not save your configuration.{user_interface.Style.RESET_ALL}")
        sys.exit("Failed to save user configuration.")
    return user_config




# --- Main Application Logic (Async now) ---
async def perform_proactive_checks(user_config, gemini_llm_model) -> tuple[bool, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Performs one cycle of proactive checks for Gmail and Calendar.
    Processes fetched data with LLM.
    Returns (can_continue_without_auth, important_emails_llm_data, processed_events_llm_data)
    """
    print(f"\n{user_interface.Style.DIM}Performing proactive checks at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...{user_interface.Style.RESET_ALL}")

    user_id = user_config.get(config_manager.USER_EMAIL_KEY)
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a busy professional")
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY, "important tasks and communications")

    gmail_base_url = user_config.get(config_manager.GMAIL_MCP_URL_KEY)
    calendar_base_url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY)

    all_fetched_raw_messages = []
    auth_action_required_overall = False # Flag if any service needs auth

    # --- Gmail Check ---
    if gmail_base_url and user_id and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off":
        user_interface.print_header("Checking Gmail")
        email_cycle_successful_for_timestamp_update = False
        auth_action_required_for_gmail = False
        try:
            async with McpSessionManager(gmail_base_url, user_id, "gmail") as gmail_manager:
                if not gmail_manager.session:
                    print(f"{user_interface.Fore.RED}Failed to establish Gmail MCP session.{user_interface.Style.RESET_ALL}")
                else:
                    # print(f"Gmail tools available (first 5): {list(gmail_manager.tools.keys())[:5]}...")

                    twenty_four_hours_ago_utc = datetime.now(timezone.utc) - timedelta(hours=24)
                    # last_check_ts_str = user_config.get(config_manager.LAST_EMAIL_CHECK_KEY)
                    # query_start_dt = twenty_four_hours_ago_utc # Default to 24h
                    # if last_check_ts_str:
                    #     last_check_dt = datetime.fromisoformat(last_check_ts_str)
                    #     # Query for emails newer than last check, but not older than 24h
                    #     query_start_dt = max(last_check_dt, twenty_four_hours_ago_utc)

                    query_since_timestamp = int(twenty_four_hours_ago_utc.timestamp()) # Sticking to 24h for now
                    gmail_query = f"is:unread after:{query_since_timestamp}"

                    base_fetch_params = {
                        "query": gmail_query, "max_results": 10, "include_payload": True
                    }
                    current_page_token = None
                    max_pages_to_fetch = 3
                    pages_fetched = 0

                    while pages_fetched < max_pages_to_fetch:
                        pages_fetched += 1
                        current_fetch_params = base_fetch_params.copy()
                        if current_page_token:
                            current_fetch_params["page_token"] = current_page_token

                        # print(f"Attempting GMAIL_FETCH_EMAILS (Page {pages_fetched}) with params: {current_fetch_params}")
                        email_result_page = await gmail_manager.ensure_auth_and_call_tool("GMAIL_FETCH_EMAILS", current_fetch_params)

                        if isinstance(email_result_page, dict) and email_result_page.get("needs_user_action"):
                            print(f"{user_interface.Fore.YELLOW}Gmail requires authentication. Please follow instructions and re-run.{user_interface.Style.RESET_ALL}")
                            auth_action_required_for_gmail = True
                            auth_action_required_overall = True
                            break
                        elif isinstance(email_result_page, dict) and email_result_page.get("error"):
                            print(f"{user_interface.Fore.RED}Error fetching Gmail emails page {pages_fetched}: {email_result_page.get('error')}{user_interface.Style.RESET_ALL}")
                            current_page_token = None
                            break
                        elif email_result_page and hasattr(email_result_page, 'content'):
                            if email_result_page.content:
                                for item in email_result_page.content:
                                    text_content = getattr(item, 'text', None)
                                    if text_content:
                                        try:
                                            email_data_json_page = json.loads(text_content)
                                            if email_data_json_page.get("successful") is True:
                                                messages_on_page = email_data_json_page.get("data", {}).get("messages", [])
                                                if messages_on_page:
                                                    # print(f"Found {len(messages_on_page)} email(s) on page {pages_fetched}.")
                                                    all_fetched_raw_messages.extend(messages_on_page)
                                                current_page_token = email_data_json_page.get("data", {}).get("nextPageToken")
                                                if not current_page_token: break
                                            else:
                                                error_from_tool = email_data_json_page.get('error', 'Unknown error from GMAIL_FETCH_EMAILS tool.')
                                                print(f"{user_interface.Fore.RED}Composio GMAIL_FETCH_EMAILS reported not successful for page {pages_fetched}: {error_from_tool}{user_interface.Style.RESET_ALL}")
                                                current_page_token = None; break
                                        except json.JSONDecodeError:
                                            print(f"{user_interface.Fore.RED}Could not parse email page {pages_fetched} as JSON.{user_interface.Style.RESET_ALL}")
                                            current_page_token = None; break
                                    else: current_page_token = None; break
                            else: current_page_token = None; break
                        else: current_page_token = None; break
                        if not current_page_token: break

                    if not auth_action_required_for_gmail:
                        email_cycle_successful_for_timestamp_update = True

            if auth_action_required_for_gmail:
                return False, [], [] # Signal main to exit for auth

            if email_cycle_successful_for_timestamp_update:
                config_manager.set_last_email_check_timestamp()
                print(f"{user_interface.Fore.GREEN}Gmail check complete. {len(all_fetched_raw_messages)} unread email(s) in last 24h fetched.{user_interface.Style.RESET_ALL}")


        except Exception as e:
            print(f"{user_interface.Fore.RED}Outer error during Gmail processing: {e}{user_interface.Style.RESET_ALL}")
            # traceback.print_exc()
    else:
        if user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off":
             print(f"{user_interface.Fore.YELLOW}Gmail MCP URL or User ID not configured. Skipping Gmail checks.{user_interface.Style.RESET_ALL}")

    # --- Process Gmail with LLM ---
    important_emails_llm_data = []
    if all_fetched_raw_messages and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") == "important":
        # user_interface.print_header(f"Processing {len(all_fetched_raw_messages)} Gmail messages with LLM")
        processed_emails_from_llm = await llm_processor.process_emails_with_llm(
            gemini_llm_model, all_fetched_raw_messages, user_persona, user_priorities
        )
        if processed_emails_from_llm:
            for pe_data in processed_emails_from_llm:
                if pe_data.get('is_important'):
                    important_emails_llm_data.append(pe_data)
            # print(f"LLM identified {len(important_emails_llm_data)} important email(s).")
    elif user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") == "all":
        # If "all", treat all fetched as "important" for display, but LLM might not have summarized
        for raw_email in all_fetched_raw_messages:
             important_emails_llm_data.append({
                 "original_email_data": raw_email,
                 "is_important": True, # For display purposes
                 "summary": raw_email.get("snippet", "No summary available."), # Use snippet if no LLM summary
                 "suggested_actions": ["View full email", "Mark as read", "Delete"] # Generic actions
             })
        # print(f"Displaying all {len(important_emails_llm_data)} fetched emails (preference: all).")


    # --- Calendar Check ---
    raw_calendar_events = []
    auth_action_required_for_calendar = False
    if calendar_base_url and user_id and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
        user_interface.print_header("Checking Calendar")
        try:
            async with McpSessionManager(calendar_base_url, user_id, "googlecalendar") as calendar_manager:
                if not calendar_manager.session:
                    print(f"{user_interface.Fore.RED}Failed to establish Calendar MCP session.{user_interface.Style.RESET_ALL}")
                else:
                    # print(f"Calendar tools available (first 5): {list(calendar_manager.tools.keys())[:5]}...")
                    now_utc = datetime.now(timezone.utc)
                    time_min_str = now_utc.isoformat().replace("+00:00", "Z")
                    time_max_str = (now_utc + timedelta(days=1)).isoformat().replace("+00:00", "Z")
                    calendar_fetch_params = {
                        "calendarId": "primary", "timeMin": time_min_str,
                        "timeMax": time_max_str, "max_results": 10,
                        "singleEvents": True, "order_by": "startTime"
                    }
                    # print(f"Attempting GOOGLECALENDAR_FIND_EVENT with params: {calendar_fetch_params}")
                    event_result = await calendar_manager.ensure_auth_and_call_tool("GOOGLECALENDAR_FIND_EVENT", calendar_fetch_params)

                    if isinstance(event_result, dict) and event_result.get("needs_user_action"):
                        print(f"{user_interface.Fore.YELLOW}Google Calendar requires authentication. Please follow instructions and re-run.{user_interface.Style.RESET_ALL}")
                        auth_action_required_for_calendar = True
                        auth_action_required_overall = True
                    elif isinstance(event_result, dict) and event_result.get("error"):
                        print(f"{user_interface.Fore.RED}Error fetching Calendar events: {event_result.get('error')}{user_interface.Style.RESET_ALL}")
                    elif event_result and hasattr(event_result, 'content'):
                        if event_result.content:
                            for item in event_result.content:
                                text_content = getattr(item, 'text', None)
                                if text_content:
                                    try:
                                        event_data_json = json.loads(text_content)
                                        actual_events = event_data_json.get("data",{}).get("event_data",{}).get("event_data",[])
                                        if actual_events:
                                            raw_calendar_events.extend(actual_events)
                                    except json.JSONDecodeError:
                                        print(f"{user_interface.Fore.RED}Could not parse calendar item text as JSON.{user_interface.Style.RESET_ALL}")
                        print(f"{user_interface.Fore.GREEN}Calendar check complete. {len(raw_calendar_events)} event(s) in next 24h fetched.{user_interface.Style.RESET_ALL}")

            if auth_action_required_for_calendar:
                return False, important_emails_llm_data, [] # Signal exit for auth

        except Exception as e:
            print(f"{user_interface.Fore.RED}Error during Calendar processing: {e}{user_interface.Style.RESET_ALL}")
            # traceback.print_exc()
    else:
        if user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
            print(f"{user_interface.Fore.YELLOW}Calendar MCP URL or User ID not configured. Skipping Calendar checks.{user_interface.Style.RESET_ALL}")

    if auth_action_required_overall: # If any service triggered auth, exit now
        return False, important_emails_llm_data, raw_calendar_events

    # --- Process Calendar with LLM ---
    actionable_events_llm_data = [] # New list for only actionable events
    if raw_calendar_events and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
        processed_events_from_llm_temp = await llm_processor.process_calendar_events_with_llm(
            gemini_llm_model, raw_calendar_events, user_persona, user_priorities, user_config
        )
        if processed_events_from_llm_temp:
            for pe_data in processed_events_from_llm_temp:
                if pe_data.get('suggested_actions'): # Only include if LLM gave actions
                    actionable_events_llm_data.append(pe_data)

    # --- Send Notifications ---
    notification_sent_this_cycle = False # Track if a notification was actually sent
    num_imp_emails = len(important_emails_llm_data)
    num_act_events = len(actionable_events_llm_data) # Use this for notification

    if user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off" or \
       user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
        if num_imp_emails > 0 or num_act_events > 0 : # Or use len(raw_calendar_events) if just notifying about any event
            notif_title = "Proactive Assistant Update"
            notif_message_parts = []
            if num_imp_emails > 0 and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("email", "off") != "off":
                notif_message_parts.append(f"{num_imp_emails} important email(s)")
            if num_act_events > 0 and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off": # Check pref again
                notif_message_parts.append(f"{num_act_events} upcoming event(s) with suggestions")
            elif len(raw_calendar_events) > 0 and user_config.get(config_manager.NOTIFICATION_PREFS_KEY, {}).get("calendar", "off") != "off":
                 notif_message_parts.append(f"{len(raw_calendar_events)} upcoming event(s)")


            if notif_message_parts:
                # --- Determine paths for notification action ---
                python_executable = sys.executable
                script_file_path_obj = Path(__file__).resolve() # Path object to assistant.py
                work_dir_obj = script_file_path_obj.parent    # Path object to project directory

                # The action script will be assistant.py itself, and we'll add the flag later
                # when constructing the command for osascript.
                # For now, script_to_run_on_action is just the path to assistant.py.
                # The --from-notification flag will be handled by the command_to_run_in_terminal construction.

                notifier.send_macos_notification(
                    notif_title,
                    ", ".join(notif_message_parts) + " requiring attention.",
                    python_executable_for_action=str(python_executable),
                    script_to_run_on_action=str(script_file_path_obj), # Just the script path
                    working_dir_for_action=str(work_dir_obj)
                )
                notification_sent_this_cycle = True

        else: # No important items
            if sys.stdin.isatty(): # Only print if interactive
                print(f"{user_interface.Style.DIM}No new important items for notification this cycle.{user_interface.Style.RESET_ALL}")
            else: # Log for non-interactive runs
                print(f"NOTIF_LOG: No new important items for notification this cycle at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # +++++++++++++ SAVE ACTIONABLE DATA IF NOTIFICATION WAS SENT +++++++++++++
    if notification_sent_this_cycle: # Only save if we actually notified the user
        config_manager.save_actionable_data(important_emails_llm_data, actionable_events_llm_data)
    else: # If no notification, clear any old actionable data
        config_manager.clear_actionable_data()

    print(f"{user_interface.Style.DIM}Proactive checks cycle complete.{user_interface.Style.RESET_ALL}")
    return True, important_emails_llm_data, actionable_events_llm_data # Return the filtered list


async def handle_draft_email_reply(
    gemini_llm_model,
    chosen_email_data: Dict[str, Any],
    initial_llm_action_text: str,
    user_config: Dict[str, Any]
    # No McpSessionManagers passed in directly; they will be created internally for actions
) -> bool: # Returns True if action was processed (even if cancelled by user), False on critical error
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a professional")
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY, "key tasks")

    # Get URLs and user_id for potential MCP calls
    gmail_mcp_url = user_config.get(config_manager.GMAIL_MCP_URL_KEY)
    calendar_mcp_url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY) # For finding slots
    user_id = user_config.get(config_manager.USER_EMAIL_KEY)

    current_draft_info = None
    available_slots_for_llm: Optional[List[Dict[str, str]]] = None

    # --- Conditionally Find Free Slots ---
    if calendar_mcp_url and user_id and \
       ("availability" in initial_llm_action_text.lower() or \
        "times" in initial_llm_action_text.lower() or \
        "slots" in initial_llm_action_text.lower() or \
        "propose" in initial_llm_action_text.lower() or \
        "slot" in initial_llm_action_text.lower() or \
        "time" in initial_llm_action_text.lower() or \
        "free" in initial_llm_action_text.lower() or \
        "suggest time" in initial_llm_action_text.lower()):

        if user_interface.get_yes_no_input("Do you want to check your calendar for free slots to suggest in this email reply?", default_yes=True):
            date_str = user_interface.get_user_input("Enter date to find free slots (YYYY-MM-DD, 'today', 'tomorrow')", default="today")
            duration_str = user_interface.get_user_input("Desired meeting duration for slots (e.g., 30m, 1h)", default="30m")

            meeting_duration_minutes = 30
            try:
                if 'h' in duration_str:
                    parts = duration_str.split('h')
                    hours = int(parts[0])
                    minutes_part = parts[1].replace('m','')
                    minutes = int(minutes_part) if minutes_part else 0
                    meeting_duration_minutes = (hours * 60) + minutes
                elif 'm' in duration_str:
                    meeting_duration_minutes = int(duration_str.replace('m',''))
            except ValueError: meeting_duration_minutes = 30 # Default on parse error

            target_date = None
            now_ist = datetime.now(calendar_utils.IST)
            if date_str.lower() == "today": target_date = now_ist.date()
            elif date_str.lower() == "tomorrow": target_date = (now_ist + timedelta(days=1)).date()
            else:
                try: target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError: print(f"{user_interface.Fore.RED}Invalid date format for finding slots.{user_interface.Style.RESET_ALL}")

            if target_date:
                time_min_dt = datetime(target_date.year, target_date.month, target_date.day, 9, 0, 0, tzinfo=calendar_utils.IST)
                time_max_dt = datetime(target_date.year, target_date.month, target_date.day, 18, 0, 0, tzinfo=calendar_utils.IST)
                time_min_iso = calendar_utils.format_datetime_to_iso_ist(time_min_dt)
                time_max_iso = calendar_utils.format_datetime_to_iso_ist(time_max_dt)
                work_start_h = user_config.get(config_manager.WORK_START_HOUR_KEY, 9) # Default 9
                work_end_h = user_config.get(config_manager.WORK_END_HOUR_KEY, 18)   # Default 18
                parsed_target_date_str = target_date.strftime("%Y-%m-%d")

                print(f"{user_interface.Style.DIM}Establishing session to find free slots...{user_interface.Style.RESET_ALL}")
                async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-findslots-for-email") as slot_finder_manager:
                    if slot_finder_manager.session:
                        free_slots_result = await slot_finder_manager.get_calendar_free_slots(
                            time_min_iso_ist=time_min_iso, # Corrected from just time_min_iso
                            time_max_iso_ist=time_max_iso, # Corrected from just time_max_iso
                            meeting_duration_minutes=meeting_duration_minutes,
                            user_work_start_hour=work_start_h, # Pass configured hours
                            user_work_end_hour=work_end_h      # Pass configured hours
                        )
                        if free_slots_result.get("successful"):
                            available_slots_for_llm = free_slots_result.get("free_slots", [])
                            if available_slots_for_llm:
                                print(f"{user_interface.Fore.GREEN}Found {len(available_slots_for_llm)} free slots. They will be provided to the LLM.{user_interface.Style.RESET_ALL}")
                                user_interface.display_free_slots(available_slots_for_llm, parsed_target_date_str)
                            else:
                                print(f"{user_interface.Fore.YELLOW}No free slots found for the specified criteria.{user_interface.Style.RESET_ALL}")
                        else:
                            print(f"{user_interface.Fore.RED}Error finding free slots: {free_slots_result.get('error')}{user_interface.Style.RESET_ALL}")
                    else:
                        print(f"{user_interface.Fore.RED}Could not establish session to find free slots.{user_interface.Style.RESET_ALL}")
            # If target_date was not set due to invalid input, available_slots_for_llm remains None

    # Initial draft generation
    print(f"\n{user_interface.Style.DIM}Drafting reply for '{chosen_email_data['original_email_data'].get('subject', 'N/A')}'...{user_interface.Style.RESET_ALL}")
    current_draft_info = await llm_processor.draft_email_reply_with_llm(
        gemini_llm_model,
        chosen_email_data['original_email_data'],
        initial_llm_action_text,
        user_persona,
        user_priorities,
        user_edit_instructions=None,
        available_slots=available_slots_for_llm
    )

    # Edit/Send/Cancel loop
    while True:
        if not current_draft_info or current_draft_info.get("error"):
            error_msg = current_draft_info.get("error", "Failed to generate draft.") if current_draft_info else "Failed to generate draft."
            print(f"{user_interface.Fore.RED}Error: {error_msg}{user_interface.Style.RESET_ALL}")
            return False

        draft_body = current_draft_info.get("body")
        draft_subject = current_draft_info.get("subject")

        confirmation_choice = user_interface.get_send_edit_cancel_confirmation(
            f"Subject: {draft_subject}\n\n{draft_body}",
            service_name="Email Reply"
        )

        if confirmation_choice == "send_reply":
            recipient_for_reply = current_draft_info.get("recipient_email_for_reply")
            original_thread_id = current_draft_info.get("original_thread_id")

            if not recipient_for_reply or recipient_for_reply == "Unknown Sender" or "@" not in recipient_for_reply:
                 print(f"{user_interface.Fore.RED}Cannot send reply: Valid recipient email not found. Found: '{recipient_for_reply}'{user_interface.Style.RESET_ALL}")
                 return False
            if not original_thread_id or not draft_body:
                 print(f"{user_interface.Fore.RED}Critical reply information missing (thread ID or body). Cannot send.{user_interface.Style.RESET_ALL}")
                 return False
            if not gmail_mcp_url or not user_id:
                print(f"{user_interface.Fore.RED}Gmail configuration missing. Cannot send reply.{user_interface.Style.RESET_ALL}")
                return False

            print(f"{user_interface.Style.DIM}Establishing session to send Gmail reply...{user_interface.Style.RESET_ALL}")
            async with McpSessionManager(gmail_mcp_url, user_id, "gmail-action-send-reply") as exec_gmail_manager:
                if not exec_gmail_manager.session:
                    print(f"{user_interface.Fore.RED}Failed to establish Gmail session for sending reply.{user_interface.Style.RESET_ALL}")
                    return False

                send_reply_outcome = await exec_gmail_manager.reply_to_gmail_thread(
                    thread_id=original_thread_id,
                    recipient_email=recipient_for_reply,
                    message_body=draft_body
                )

                if send_reply_outcome.get("successful"):
                    print(f"{user_interface.Fore.GREEN}Success! {send_reply_outcome.get('message', 'Reply sent.')}{user_interface.Style.RESET_ALL}")
                    # +++++++++++++ MARK THREAD AS READ +++++++++++++
                    # original_message_id = chosen_email_data.get("original_email_data", {}).get("messageId") # We need threadId now
                    original_thread_id_for_mark = chosen_email_data.get("original_email_data", {}).get("threadId")

                    if original_thread_id_for_mark: # Make sure we have a threadId
                        print(f"{user_interface.Style.DIM}Attempting to mark original email thread ({original_thread_id_for_mark}) as read...{user_interface.Style.RESET_ALL}")

                        mark_read_outcome = await exec_gmail_manager.mark_thread_as_read( # Call new method
                            thread_id=original_thread_id_for_mark
                        )

                        if mark_read_outcome.get("successful"):
                            print(f"{user_interface.Fore.GREEN}Original email thread marked as read.{user_interface.Style.RESET_ALL}")
                        else:
                            print(f"{user_interface.Fore.YELLOW}Could not mark original email thread as read: {mark_read_outcome.get('error')}{user_interface.Style.RESET_ALL}")
                    else:
                        print(f"{user_interface.Fore.YELLOW}Could not find threadId for original email to mark as read.{user_interface.Style.RESET_ALL}")
                    # ++++++++++++++++++++++++++++++++++++++++++++++++
                    return True
                else:
                    error_msg = send_reply_outcome.get('error', 'Failed to send reply via MCP.')
                    print(f"{user_interface.Fore.RED}MCP Error sending reply: {error_msg}{user_interface.Style.RESET_ALL}")
                if send_reply_outcome.get("successful"):
                    return True # The 'send_reply' action was successful
                else:
                    return False # The 'send_reply' action failed

        elif confirmation_choice == "edit":
            user_edit_instructions = user_interface.get_user_input(f"{user_interface.Fore.CYAN}Your edit instructions (or type your full new draft):{user_interface.Style.RESET_ALL}")
            print(f"{user_interface.Style.DIM}Re-drafting with your instructions...{user_interface.Style.RESET_ALL}")
            current_draft_info = await llm_processor.draft_email_reply_with_llm(
                gemini_llm_model,
                chosen_email_data['original_email_data'],
                initial_llm_action_text,
                user_persona,
                user_priorities,
                user_edit_instructions=user_edit_instructions,
                available_slots=available_slots_for_llm
            )
            # Loop continues

        elif confirmation_choice == "cancel":
            print(f"{user_interface.Fore.YELLOW}Drafting and replying cancelled.{user_interface.Style.RESET_ALL}")
            return True

        else:
            print(f"{user_interface.Fore.RED}Unknown confirmation choice in email reply handler.{user_interface.Style.RESET_ALL}")
            return False

# assistant.py

async def handle_delete_calendar_event(
    chosen_event_data: Dict[str, Any],
    user_config: Dict[str, Any]
    # REMOVE calendar_mcp_manager from parameters
) -> bool:
    original_event_details = chosen_event_data.get("original_event_data", {})
    event_id_to_delete = original_event_details.get("id")
    event_title = original_event_details.get("summary", "Unknown Event")

    if not event_id_to_delete:
        print(f"{user_interface.Fore.RED}Error: Could not find Event ID for '{event_title}'. Cannot delete.{user_interface.Style.RESET_ALL}")
        return False

    confirm_delete = user_interface.get_confirmation(
        f"Are you sure you want to delete the event: '{event_title}' (ID: {event_id_to_delete})?",
        destructive=True
    )

    if confirm_delete:
        calendar_mcp_url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY)
        user_id = user_config.get(config_manager.USER_EMAIL_KEY)
        if not calendar_mcp_url or not user_id:
            print(f"{user_interface.Fore.RED}Calendar configuration missing for delete action.{user_interface.Style.RESET_ALL}")
            return False

        print(f"{user_interface.Style.DIM}Establishing session to delete calendar event '{event_title}'...{user_interface.Style.RESET_ALL}")
        async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-action-delete-EXECUTE") as exec_cal_manager:
            if not exec_cal_manager.session:
                print(f"{user_interface.Fore.RED}Failed to establish session for deleting event.{user_interface.Style.RESET_ALL}")
                return False

            delete_outcome = await exec_cal_manager.delete_calendar_event(event_id=event_id_to_delete)

        if delete_outcome.get("successful"):
            print(f"{user_interface.Fore.GREEN}Success! {delete_outcome.get('message', f'Event {event_title} deleted.')}{user_interface.Style.RESET_ALL}")
            return True
        else:
            error_msg = delete_outcome.get('error', 'Failed to delete event via MCP.')
            print(f"{user_interface.Fore.RED}MCP Error deleting event: {error_msg}{user_interface.Style.RESET_ALL}")
            return False
    else:
        print(f"{user_interface.Fore.YELLOW}Event deletion cancelled by user.{user_interface.Style.RESET_ALL}")
        return True

async def handle_update_calendar_event(
    chosen_event_data: Dict[str, Any],
    user_config: Dict[str, Any]
    # No McpSessionManager passed directly for the whole function's lifetime
) -> bool:
    original_event_details = chosen_event_data.get("original_event_data", {})
    event_id_to_update = original_event_details.get("id")
    event_title = original_event_details.get("summary", "Unknown Event")

    if not event_id_to_update:
        print(f"{user_interface.Fore.RED}Error: Could not find Event ID for '{event_title}'. Cannot update.{user_interface.Style.RESET_ALL}")
        return False

    # Get calendar URL and user_id for potential MCP calls
    calendar_mcp_url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY)
    user_id = user_config.get(config_manager.USER_EMAIL_KEY)
    if not calendar_mcp_url or not user_id:
        print(f"{user_interface.Fore.RED}Calendar configuration missing. Cannot perform calendar actions.{user_interface.Style.RESET_ALL}")
        return False

    # Loop to allow finding free slots and then returning to edit/confirm updates
    # This loop primarily collects data into `current_update_payload`
    current_update_payload: Optional[Dict[str, Any]] = {} # Start with empty updates

    while True:
        # Pass current_update_payload to UI so it can show current staged changes
        # get_event_update_choices needs to be adapted to take current_update_payload
        # and merge new edits into it or use its values as defaults.
        # For simplicity now, let's assume get_event_update_choices starts fresh each time
        # OR that it returns the *complete set of updates* the user wants for the save action.
        # The version of get_event_update_choices you have does build up 'updates' internally.

        ui_outcome = user_interface.get_event_update_choices( # This function builds the 'updates' dict
            event_title,
            original_event_details
        )

        if not ui_outcome: # User cancelled the entire update process from the menu
            print(f"{user_interface.Fore.YELLOW}Event update cancelled.{user_interface.Style.RESET_ALL}")
            return True # User action, not a failure

        if isinstance(ui_outcome, dict) and ui_outcome.get("trigger_action") == "find_free_slots":
            # ... (logic to ask for date/duration for find_free_slots) ...
            # ... (call mcp_handler.get_calendar_free_slots by opening a temp McpSessionManager) ...
            print(f"\n{user_interface.Style.DIM}Finding free slots for '{event_title}'...{user_interface.Style.RESET_ALL}")
            date_str = user_interface.get_user_input("Enter date (YYYY-MM-DD, 'today', 'tomorrow')", default="today")
            duration_str = user_interface.get_user_input("Desired duration (e.g., 30m, 1h)", default="30m")
            # Parse date_str and duration_str...
            # ... (same parsing as in handle_create_calendar_event) ...
            meeting_duration_minutes = 30 # Placeholder
            time_min_iso, time_max_iso, parsed_target_date_str = "","","" # Placeholder
            # (Full date/duration parsing and time_min/max construction needed here)
            try:
                _meeting_duration_minutes = 30
                if 'h' in duration_str:
                    _hours = int(duration_str.split('h')[0])
                    _minutes_part = duration_str.split('h')[1].replace('m','')
                    _minutes = int(_minutes_part) if _minutes_part else 0
                    _meeting_duration_minutes = (_hours * 60) + _minutes
                elif 'm' in duration_str:
                    _meeting_duration_minutes = int(duration_str.replace('m',''))

                _target_date = None
                _now_ist = datetime.now(calendar_utils.IST)
                if date_str.lower() == "today": _target_date = _now_ist.date()
                elif date_str.lower() == "tomorrow": _target_date = (_now_ist + timedelta(days=1)).date()
                else: _target_date = datetime.strptime(date_str, "%Y-%m-%d").date()

                _time_min_dt = datetime(_target_date.year, _target_date.month, _target_date.day, 9,0,0, tzinfo=calendar_utils.IST)
                _time_max_dt = datetime(_target_date.year, _target_date.month, _target_date.day, 18,0,0, tzinfo=calendar_utils.IST)
                time_min_iso = calendar_utils.format_datetime_to_iso_ist(_time_min_dt)
                time_max_iso = calendar_utils.format_datetime_to_iso_ist(_time_max_dt)
                work_start_h = user_config.get(config_manager.WORK_START_HOUR_KEY, 9) # Default 9
                work_end_h = user_config.get(config_manager.WORK_END_HOUR_KEY, 18)   # Default 18
                parsed_target_date_str = _target_date.strftime("%Y-%m-%d")
                meeting_duration_minutes = _meeting_duration_minutes


                async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-findslots-in-update") as slot_finder_manager:
                    if slot_finder_manager.session:
                        free_slots_result = await slot_finder_manager.get_calendar_free_slots(
                            time_min_iso_ist=time_min_iso, # Corrected from just time_min_iso
                            time_max_iso_ist=time_max_iso, # Corrected from just time_max_iso
                            meeting_duration_minutes=meeting_duration_minutes,
                            user_work_start_hour=work_start_h, # Pass configured hours
                            user_work_end_hour=work_end_h      # Pass configured hours
                        )

                        if free_slots_result.get("successful"):
                            user_interface.display_free_slots(free_slots_result.get("free_slots", []), parsed_target_date_str)
                        else:
                            print(f"{user_interface.Fore.RED}Error finding free slots: {free_slots_result.get('error')}{user_interface.Style.RESET_ALL}")
                    else:
                        print(f"{user_interface.Fore.RED}Could not establish session to find free slots.{user_interface.Style.RESET_ALL}")
            except ValueError:
                 print(f"{user_interface.Fore.RED}Invalid date or duration for finding slots.{user_interface.Style.RESET_ALL}")


            print(f"\n{user_interface.Style.DIM}You can now use this information to set 'Start Datetime' and 'Duration'.{user_interface.Style.RESET_ALL}")
            current_update_payload = {} # Reset updates as user will re-enter or confirm them
            continue # Go back to "What would you like to update?" menu

        # If it's not a trigger, it's the 'updates' dictionary from user pressing 's'
        final_updates_for_api = ui_outcome

        print(f"{user_interface.Style.DIM}Establishing session to update calendar event '{event_title}'...{user_interface.Style.RESET_ALL}")
        async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-action-update-EXECUTE") as exec_cal_manager:
            if not exec_cal_manager.session:
                print(f"{user_interface.Fore.RED}Failed to establish session for final update.{user_interface.Style.RESET_ALL}")
                return False # Critical failure

            print(f"{user_interface.Style.DIM}Attempting to commit updates: {final_updates_for_api}...{user_interface.Style.RESET_ALL}")
            update_outcome = await exec_cal_manager.update_calendar_event(
                event_id=event_id_to_update,
                updates=final_updates_for_api
            )

        if update_outcome.get("successful"):
            print(f"{user_interface.Fore.GREEN}Success! {update_outcome.get('message', f'Event {event_title} updated.')}{user_interface.Style.RESET_ALL}")
            return True
        else:
            error_msg = update_outcome.get('error', 'Failed to update event via MCP.')
            print(f"{user_interface.Fore.RED}MCP Error updating event: {error_msg}{user_interface.Style.RESET_ALL}")
            return False



async def handle_create_calendar_event(
    gemini_llm_model,
    llm_suggestion_text: str,
    original_context_text: Optional[str],
    user_config: Dict[str, Any]
    # No McpSessionManager passed directly
) -> bool:
    user_persona = user_config.get(config_manager.USER_PERSONA_KEY, "a professional")
    user_priorities = user_config.get(config_manager.USER_PRIORITIES_KEY, "key tasks")
    current_time_for_llm_context = datetime.now(timezone.utc).isoformat()

    calendar_mcp_url = user_config.get(config_manager.CALENDAR_MCP_URL_KEY) # For MCP calls
    user_id = user_config.get(config_manager.USER_EMAIL_KEY)
    if not calendar_mcp_url or not user_id:
        print(f"{user_interface.Fore.RED}Calendar configuration missing. Cannot create event.{user_interface.Style.RESET_ALL}")
        return False

    print(f"\n{user_interface.Style.DIM}Assistant is parsing details for new event based on: '{llm_suggestion_text}'...{user_interface.Style.RESET_ALL}")

    current_event_creation_details = await llm_processor.parse_event_creation_details_from_suggestion(
        gemini_llm_model=gemini_llm_model,
        llm_suggestion_text=llm_suggestion_text,
        original_context_text=original_context_text,
        user_persona=user_persona,
        user_priorities=user_priorities,
        current_datetime_iso=current_time_for_llm_context
    )

    if not current_event_creation_details or current_event_creation_details.get("error"):
        error_msg = current_event_creation_details.get("error", "Failed to parse event creation details.") if current_event_creation_details else "LLM parsing returned None."
        print(f"{user_interface.Fore.RED}Could not proceed: {error_msg}{user_interface.Style.RESET_ALL}")
        return False

    while True:
        final_event_details_for_api_or_trigger = user_interface.get_event_creation_confirmation_and_edits(
            current_event_creation_details
        )

        if not final_event_details_for_api_or_trigger:
            print(f"{user_interface.Fore.YELLOW}Event creation cancelled by user.{user_interface.Style.RESET_ALL}")
            return True

        if isinstance(final_event_details_for_api_or_trigger, dict) and \
           final_event_details_for_api_or_trigger.get("trigger_action") == "find_free_slots":

            print(f"\n{user_interface.Style.DIM}Finding free slots for the new event...{user_interface.Style.RESET_ALL}")
            date_str = user_interface.get_user_input("Enter date (YYYY-MM-DD, 'today', 'tomorrow')", default="today")
            duration_str = user_interface.get_user_input("Desired duration (e.g., 30m, 1h)",
                default=f"{current_event_creation_details.get('event_duration_hour',0)}h{current_event_creation_details.get('event_duration_minutes',30)}m")

            meeting_duration_minutes = 30
            try:
                if 'h' in duration_str:
                    _h_part = duration_str.split('h')[0]
                    _m_part = duration_str.split('h')[1].replace('m','') if len(duration_str.split('h')) > 1 else '0'
                    meeting_duration_minutes = (int(_h_part) * 60) + (int(_m_part) if _m_part else 0)
                elif 'm' in duration_str: meeting_duration_minutes = int(duration_str.replace('m',''))
            except ValueError: meeting_duration_minutes = 30

            target_date = None; now_ist = datetime.now(calendar_utils.IST)
            if date_str.lower() == "today": target_date = now_ist.date()
            elif date_str.lower() == "tomorrow": target_date = (now_ist + timedelta(days=1)).date()
            else:
                try: target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError: print(f"{user_interface.Fore.RED}Invalid date format.{user_interface.Style.RESET_ALL}"); continue

            if target_date:
                time_min_dt = datetime(target_date.year, target_date.month, target_date.day, 9,0,0, tzinfo=calendar_utils.IST)
                time_max_dt = datetime(target_date.year, target_date.month, target_date.day, 18,0,0, tzinfo=calendar_utils.IST)
                time_min_iso = calendar_utils.format_datetime_to_iso_ist(time_min_dt)
                time_max_iso = calendar_utils.format_datetime_to_iso_ist(time_max_dt)
                work_start_h = user_config.get(config_manager.WORK_START_HOUR_KEY, 9) # Default 9
                work_end_h = user_config.get(config_manager.WORK_END_HOUR_KEY, 18)   # Default 18
                parsed_target_date_str = target_date.strftime("%Y-%m-%d")

                print(f"{user_interface.Style.DIM}Establishing session to find free slots...{user_interface.Style.RESET_ALL}")
                async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-findslots-for-create") as slot_finder_manager:
                    if slot_finder_manager.session:
                        free_slots_result = await slot_finder_manager.get_calendar_free_slots(
                            time_min_iso_ist=time_min_iso, # Corrected from just time_min_iso
                            time_max_iso_ist=time_max_iso, # Corrected from just time_max_iso
                            meeting_duration_minutes=meeting_duration_minutes,
                            user_work_start_hour=work_start_h,
                            user_work_end_hour=work_end_h
                        )
                        if free_slots_result.get("successful"):
                            slots_found = free_slots_result.get("free_slots", [])
                            user_interface.display_free_slots(slots_found, parsed_target_date_str)
                            if slots_found and user_interface.get_yes_no_input("Use one of these slots for the event?", default_yes=False):
                                slot_choice_str = user_interface.get_user_input(f"Enter slot number (1-{len(slots_found)}) or 'n'")
                                if slot_choice_str.isdigit():
                                    slot_idx = int(slot_choice_str) - 1
                                    if 0 <= slot_idx < len(slots_found):
                                        chosen_slot_start_iso = slots_found[slot_idx]['start']
                                        dt_obj = calendar_utils.parse_iso_to_ist(chosen_slot_start_iso)
                                        if dt_obj:
                                            current_event_creation_details["start_datetime"] = dt_obj.strftime("%Y-%m-%dT%H:%M:%S")
                                            current_event_creation_details["timezone"] = "Asia/Kolkata"
                                            current_event_creation_details["event_duration_hour"] = meeting_duration_minutes // 60
                                            current_event_creation_details["event_duration_minutes"] = meeting_duration_minutes % 60
                                            print(f"{user_interface.Fore.GREEN}Event time updated from slot.{user_interface.Style.RESET_ALL}")
                        else: print(f"{user_interface.Fore.RED}Error finding slots: {free_slots_result.get('error')}{user_interface.Style.RESET_ALL}")
                    else: print(f"{user_interface.Fore.RED}Could not connect to find slots.{user_interface.Style.RESET_ALL}")
            continue # Go back to UI menu with potentially updated current_event_creation_details

        final_event_details_for_api = final_event_details_for_api_or_trigger # This is when user chose 's'

        print(f"{user_interface.Style.DIM}Establishing session to create calendar event...{user_interface.Style.RESET_ALL}")
        async with McpSessionManager(calendar_mcp_url, user_id, "googlecalendar-action-create-EXECUTE") as exec_cal_manager:
            if not exec_cal_manager.session:
                print(f"{user_interface.Fore.RED}Failed to establish session for event creation.{user_interface.Style.RESET_ALL}")
                return False

            create_outcome = await exec_cal_manager.create_calendar_event(event_details=final_event_details_for_api)

        if create_outcome.get("successful"):
            # ... (success message)
            return True
        else:
            # ... (error message)
            return False



async def main_assistant_entry(run_mode: str = "normal"):
    """Entry point for the assistant logic."""
    # --- Initial setup ---
    if not config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY):
        # For launchd, this should ideally log to a file.
        # For now, print is fine; it will go to StandardErrorPath in plist.
        print(f"ASSISTANT_ERROR: {config_manager.ENV_GOOGLE_API_KEY} not set. Exiting.")
        return 1 # Error exit code

    user_configuration = config_manager.load_user_config()

    if not user_configuration or not user_configuration.get(config_manager.USER_EMAIL_KEY):
        if sys.stdin.isatty(): # Check if running in an interactive terminal
            print("Running first-time setup for assistant...")
            user_configuration = run_signup_flow()
            if not (user_configuration and user_configuration.get(config_manager.USER_EMAIL_KEY)):
                print(f"{user_interface.Fore.RED}Signup incomplete. Exiting.{user_interface.Style.RESET_ALL}")
                return 1 # Error exit code
            user_configuration = config_manager.load_user_config() # Reload to be sure
        else:
            # Running non-interactively (e.g., by launchd) AND not configured
            print("ASSISTANT_ERROR: Not configured and not in an interactive terminal for setup. Please run manually once to configure.")
            return 1 # Error exit code

    # --- This part is fine if user_configuration is loaded ---
    if sys.stdin.isatty(): # Only print welcome back if interactive
        print(f"\n{user_interface.Fore.GREEN}Welcome back, {user_configuration.get(config_manager.USER_EMAIL_KEY)}!{user_interface.Style.RESET_ALL}")
    print(f"{user_interface.Style.DIM}Proactive Assistant Cycle Starting at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}...{user_interface.Style.RESET_ALL}")

    google_api_key = config_manager.DEV_CONFIG.get(config_manager.ENV_GOOGLE_API_KEY)
    try:
        genai.configure(api_key=google_api_key)
        gemini_llm_model = genai.GenerativeModel('gemini-2.5-flash-preview-05-20')
        if sys.stdin.isatty(): # <<<< ADDED CHECK (optional)
            print(f"{user_interface.Fore.GREEN}Gemini model initialized successfully.{user_interface.Style.RESET_ALL}")
    except Exception as e:
        print(f"{user_interface.Fore.RED}Failed to initialize Gemini model: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()
        return 1 # Error exit code
# outer action loop removed

    # --- ACTIVE DAY/HOUR CHECK FOR LAUNCHD ---
    # This check is now primary. If launchd runs it outside these times, it does nothing.
    active_days = user_configuration.get(config_manager.SCHED_ACTIVE_DAYS_KEY, [])
    active_start_hour = user_configuration.get(config_manager.SCHED_ACTIVE_START_HOUR_KEY, -1) # Use invalid defaults
    active_end_hour = user_configuration.get(config_manager.SCHED_ACTIVE_END_HOUR_KEY, -1)

    now_ist = datetime.now(calendar_utils.IST) # Use IST
    if not (now_ist.weekday() in active_days and \
            active_start_hour <= now_ist.hour < active_end_hour):
        print(f"{user_interface.Style.DIM}Current time {now_ist.strftime('%A %H:%M')} is outside active schedule ({active_days}, {active_start_hour}:00-{active_end_hour}:00). Skipping checks.{user_interface.Style.RESET_ALL}")
        return 0 # Normal exit, just not active time

    actionable_emails_list = []
    actionable_events_list = []
    can_proceed_to_interaction = False

    # --- MODIFICATION FOR RUN MODE ---
    actionable_emails_list = []
    actionable_events_list = []
    can_proceed_to_interaction = False

    if run_mode == "from_notification":
        print(f"{user_interface.Style.DIM}Loading data from last notification check...{user_interface.Style.RESET_ALL}")
        loaded_data = config_manager.load_actionable_data() # Uses default max_age
        if loaded_data:
            actionable_emails_list = loaded_data.get("emails", [])
            actionable_events_list = loaded_data.get("events", [])
            can_proceed_to_interaction = True
             # Clear the data after loading so it's not re-used if user quits and re-runs manually
            config_manager.clear_actionable_data()
        else:
            print(f"{user_interface.Fore.YELLOW}Could not load recent actionable data. Performing a fresh check...{user_interface.Style.RESET_ALL}")
            # Fall through to normal check
            # No active day/hour check here, as user explicitly clicked notification

    if not can_proceed_to_interaction: # Normal run or fallback from failed load
        # Active day/hour check for non-notification triggered runs (e.g. launchd direct call)
        if run_mode == "normal" and not (now_ist.weekday() in active_days and active_start_hour <= now_ist.hour < active_end_hour):
            print(f"{user_interface.Style.DIM}Current time {now_ist.strftime('%A %H:%M')} is outside active schedule. Skipping checks.{user_interface.Style.RESET_ALL}")
            return 0
            # No active day/hour check here, as user explicitly clicked notification

        continue_after_checks, emails_from_check, events_from_check = await perform_proactive_checks(
            user_configuration, gemini_llm_model
        )
        if not continue_after_checks: # Auth needed
            print(f"{user_interface.Fore.YELLOW}Cycle paused: user action (e.g., auth) required. Exiting this run.{user_interface.Style.RESET_ALL}")
            return 0
        actionable_emails_list = emails_from_check
        actionable_events_list = events_from_check
        can_proceed_to_interaction = True # Data is now fresh

    # --- End of MODIFICATION FOR RUN MODE ---

    if sys.stdin.isatty() and can_proceed_to_interaction:
        if not actionable_emails_list and not actionable_events_list: # Check if lists are truly empty
            print(f"\n{user_interface.Fore.GREEN}No actionable items found to interact with.{user_interface.Style.RESET_ALL}")
        else:
            first_display_of_items = True
            while True:
                action_choice_data = user_interface.display_processed_data_and_get_action(
                    actionable_emails_list,
                    actionable_events_list,
                    first_time_display=first_display_of_items
                )
                first_display_of_items = False

                first_display_of_items = False
                if not action_choice_data:
                    if not actionable_emails_list and not actionable_events_list: break
                    else: print(f"{user_interface.Fore.YELLOW}Try selection again or 'd', 'r', 'q'.{user_interface.Style.RESET_ALL}"); continue
                action_type, item_idx, action_idx_in_llm_suggestions, raw_choice = action_choice_data
                if action_type == "done": print(f"{user_interface.Fore.YELLOW}Done with actions.{user_interface.Style.RESET_ALL}"); break
                elif action_type == "quit_assistant": print(f"{user_interface.Fore.YELLOW}Quitting.{user_interface.Style.RESET_ALL}"); return 0
                elif action_type == "redisplay": first_display_of_items = True; continue
                action_succeeded_this_turn = False


                if action_type == "email":
                    chosen_email_data = actionable_emails_list[item_idx]
                    chosen_llm_action_text = chosen_email_data['suggested_actions'][action_idx_in_llm_suggestions]
                    user_interface.print_header(f"Action on Email: {chosen_email_data['original_email_data'].get('subject', 'N/A')[:40]}...")
                    print(f"{user_interface.Style.BRIGHT}Chosen LLM Suggested Action: {user_interface.Fore.GREEN}{chosen_llm_action_text}{user_interface.Style.RESET_ALL}")

                    # --- MODIFICATION FOR EMAIL ACTIONS ---
                    if "draft" in chosen_llm_action_text.lower() or \
                        "reply" in chosen_llm_action_text.lower() or \
                        "availability" in chosen_llm_action_text.lower() or \
                        "times" in chosen_llm_action_text.lower() or \
                        "slots" in chosen_llm_action_text.lower() or \
                        "propose" in chosen_llm_action_text.lower() or \
                        "suggest" in chosen_llm_action_text.lower():

                        action_succeeded_this_turn = await handle_draft_email_reply(
                            gemini_llm_model,
                            chosen_email_data,
                            chosen_llm_action_text,
                            user_configuration
                            # No McpSessionManager passed here
                        )
                    elif "create calendar event" in chosen_llm_action_text.lower() or \
                            "schedule a meeting" in chosen_llm_action_text.lower() or \
                            "schedule a meeting" in chosen_llm_action_text.lower() or \
                            "add to calendar" in chosen_llm_action_text.lower():

                        original_email_body_for_context = chosen_email_data.get("original_email_data", {}).get("messageText") or \
                                                            chosen_email_data.get("original_email_data", {}).get("snippet","No original email body available for context.")
                        action_succeeded_this_turn = await handle_create_calendar_event(
                            gemini_llm_model,
                            chosen_llm_action_text,
                            original_email_body_for_context,
                            user_configuration
                            # No McpSessionManager passed here
                        )
                    else:
                        print(f"{user_interface.Fore.MAGENTA}Action '{chosen_llm_action_text}' for email is not yet specifically implemented.{user_interface.Style.RESET_ALL}")

                    pass

                elif action_type == "event":
                    if 0 <= item_idx < len(actionable_events_list): # Redundant check if UI is correct, but safe
                        chosen_event_data = actionable_events_list[item_idx]
                        chosen_llm_action_text = chosen_event_data['suggested_actions'][action_idx_in_llm_suggestions]
                        original_event_summary_for_context = chosen_event_data.get("original_event_data", {}).get("summary", "related event")
                        user_interface.print_header(f"Action on Event: {original_event_summary_for_context[:40]}...")
                        print(f"{user_interface.Style.BRIGHT}Chosen LLM Suggested Action: {user_interface.Fore.GREEN}{chosen_llm_action_text}{user_interface.Style.RESET_ALL}")

                        # --- MODIFICATION FOR CALENDAR ACTIONS ---
                        if "update this event's details" in chosen_llm_action_text.lower() or \
                            "reschedule" in chosen_llm_action_text.lower() or \
                            "change title" in chosen_llm_action_text.lower() or \
                            "add attendee" in chosen_llm_action_text.lower() or \
                            "add google meet" in chosen_llm_action_text.lower():

                            action_succeeded_this_turn = await handle_update_calendar_event(
                                chosen_event_data,
                                user_configuration
                                # No McpSessionManager passed here
                            )
                        elif "delete" in chosen_llm_action_text.lower() or "cancel this meeting" in chosen_llm_action_text.lower():
                            action_succeeded_this_turn = await handle_delete_calendar_event(
                                chosen_event_data,
                                user_configuration
                                # No McpSessionManager passed here
                            )
                        elif "create a new event" in chosen_llm_action_text.lower() or \
                                "schedule a follow-up" in chosen_llm_action_text.lower() or \
                                "schedule a prep session" in chosen_llm_action_text.lower() or \
                                "block additional time" in chosen_llm_action_text.lower():

                            action_succeeded_this_turn = await handle_create_calendar_event(
                                gemini_llm_model,
                                chosen_llm_action_text,
                                original_event_summary_for_context,
                                user_configuration
                                # No McpSessionManager passed here
                            )
                        else:
                            print(f"{user_interface.Fore.MAGENTA}Action '{chosen_llm_action_text}' for event (ID: {chosen_event_data.get('original_event_data', {}).get('id')}) is not yet implemented.{user_interface.Style.RESET_ALL}")
                    else:
                        print(f"{user_interface.Fore.RED}Internal error: Invalid event index selected ({item_idx}).{user_interface.Style.RESET_ALL}")

                    pass

                if action_succeeded_this_turn: print(f"{user_interface.Fore.GREEN}Action '{raw_choice}' processed.{user_interface.Style.RESET_ALL}")
                elif action_type in ["email", "event"]: print(f"{user_interface.Fore.YELLOW}Action '{raw_choice}' not completed.{user_interface.Style.RESET_ALL}")
            print(f"\n{user_interface.Style.DIM}Finished interacting.{user_interface.Style.RESET_ALL}")

    elif not sys.stdin.isatty() and can_proceed_to_interaction: # Non-interactive run (launchd) that had data
        print("Non-interactive run: Proactive checks resulted in actionable items. Notifications sent if applicable.")
    elif not sys.stdin.isatty() and not can_proceed_to_interaction: # Non-interactive run, no data/error
        print("Non-interactive run: No new actionable items or cycle paused due to pending user action.")


    print(f"\n{user_interface.Style.DIM}Proactive Assistant Cycle Finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.{user_interface.Style.RESET_ALL}")
    return 0

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Proactive Assistant")
    parser.add_argument(
        "--from-notification",
        action="store_true",
        help="Indicates the script is run from a notification action."
    )
    args = parser.parse_args()

    current_run_mode = "from_notification" if args.from_notification else "normal"

    try:
        exit_code = asyncio.run(main_assistant_entry(run_mode=current_run_mode)) # Pass run_mode
        sys.exit(exit_code if isinstance(exit_code, int) else 0)
    except KeyboardInterrupt:
        print(f"\n{user_interface.Fore.YELLOW}Assistant stopped by user. Goodbye!{user_interface.Style.RESET_ALL}")
        sys.exit(0)
    except SystemExit as e: # Catch explicit sys.exit calls
        # If sys.exit was called with an int (like sys.exit(1)), use that.
        # Otherwise, if it was a plain sys.exit() (code is None), default to 0.
        sys.exit(e.code if isinstance(e.code, int) else 0)
    except Exception as e:
        print(f"{user_interface.Fore.RED}An unexpected error occurred in the main execution: {e}{user_interface.Style.RESET_ALL}")
        traceback.print_exc()
        sys.exit(1) # Error exit for cron/launchd
