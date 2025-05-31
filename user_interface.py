# user_interface.py
import sys
from typing import List, Dict, Any, Optional, Tuple
from colorama import Fore, Style, init as colorama_init
from datetime import datetime, timezone # Ensure datetime is imported


# PM Consideration: Initialize colorama once for cross-platform color support.
colorama_init(autoreset=True)

# PM Consideration: Use consistent styling for prompts and information.
# Colors help differentiate types of information:
# - Green: Success, positive actions
# - Yellow: Prompts, information requiring attention
# - Red: Errors, warnings, destructive actions
# - Cyan: Assistant's speech/questions
# - Magenta: Item titles/headers
# - Bold: Emphasis

# --- Input Functions (Moved from assistant.py) ---
def get_user_input(prompt_message: str, default: Optional[str] = None) -> str:
    display_prompt = prompt_message
    if default is not None:
        display_prompt += f" (default: {Fore.YELLOW}{default}{Style.RESET_ALL})"
    full_prompt = f"{Fore.CYAN}{display_prompt}: {Style.RESET_ALL}"
    while True:
        user_response = input(full_prompt).strip()
        if user_response:
            return user_response
        elif default is not None:
            return default
        print(f"{Fore.RED}Input cannot be empty. Please try again.{Style.RESET_ALL}")

def get_yes_no_input(prompt_message: str, default_yes: bool = True) -> bool:
    options = f"{Fore.GREEN}Y{Style.RESET_ALL}/{Fore.RED}n" if default_yes else f"{Fore.GREEN}y{Style.RESET_ALL}/{Fore.RED}N"
    prompt = f"{Fore.CYAN}{prompt_message} ({options})? : {Style.RESET_ALL}"
    while True:
        response = input(prompt).strip().lower()
        if not response:
            return default_yes
        if response in ['y', 'yes']:
            return True
        if response in ['n', 'no']:
            return False
        print(f"{Fore.RED}Invalid input. Please enter 'y' or 'n'.{Style.RESET_ALL}")

# --- Display Functions ---
def print_header(text: str):
    """Prints a styled header."""
    print(f"\n{Style.BRIGHT}{Fore.MAGENTA}--- {text} ---{Style.RESET_ALL}")

def get_confirmation(prompt_message: str, destructive: bool = False) -> bool:
    """
    Gets a yes/no confirmation from the user.
    If destructive is True, the prompt will be styled more strongly.
    """
    style_prefix = f"{Style.BRIGHT}{Fore.RED}" if destructive else f"{Fore.YELLOW}"
    prompt = f"{style_prefix}{prompt_message}{Style.RESET_ALL} ({Fore.GREEN}y{Style.RESET_ALL}/{Fore.RED}N{Style.RESET_ALL})? : "

    while True:
        response = input(prompt).strip().lower()
        if not response: # Default to No for destructive, Yes otherwise (though prompt implies N)
            return False # Safer to default to No for destructive actions if user just hits enter
        if response in ['y', 'yes']:
            return True
        if response in ['n', 'no']:
            return False
        print(f"{Fore.RED}Invalid input. Please enter 'y' or 'n'.{Style.RESET_ALL}")


def display_email_summary(index: int, email_data: Dict[str, Any]):
    """Displays a summary for a single important email."""
    original_email = email_data.get("original_email_data", {})

    sender = original_email.get("sender", "Unknown Sender")
    subject = original_email.get("subject", "No Subject")

    print(f"{Style.BRIGHT}{index}. From: {Fore.YELLOW}{sender}{Style.RESET_ALL}")
    print(f"   Subject: {Fore.YELLOW}{subject}{Style.RESET_ALL}")
    print(f"   {Fore.WHITE}Summary: {email_data.get('summary', 'N/A')}{Style.RESET_ALL}")

    actions = email_data.get('suggested_actions', [])
    if actions:
        print(f"   {Fore.GREEN}Suggested Actions:{Style.RESET_ALL}")
        for i, action_text in enumerate(actions):
            print(f"     {Style.BRIGHT}{Fore.GREEN}({chr(97 + i)}){Style.RESET_ALL} {action_text}") # a, b, c...
    print("-" * 10)

def format_datetime_for_display(iso_datetime_str: Optional[str]) -> str:
    if not iso_datetime_str:
        return "N/A"
    try:
        # Parse the ISO string with timezone
        dt_object = datetime.fromisoformat(iso_datetime_str)
        # Convert to local timezone (IST in your case, but let's make it system's local)
        # Forcing IST:
        # import pytz
        # ist = pytz.timezone('Asia/Kolkata')
        # dt_object_local = dt_object.astimezone(ist)
        # For system local (simpler if assistant runs on user's machine in their TZ):
        dt_object_local = dt_object.astimezone() # Converts to system's local timezone

        # Format to AM/PM
        # Example: "May 31, 07:30 PM" or "Jun 01, 08:00 AM"
        return dt_object_local.strftime("%b %d, %I:%M %p %Z") # e.g., May 31, 07:30 PM IST
    except ValueError:
        return iso_datetime_str # Return original if parsing fails

def display_calendar_event_summary(index: int, event_data: Dict[str, Any]):
    original_event = event_data.get("original_event_data", {})
    title = original_event.get("summary", "No Title")

    start_iso = original_event.get("start", {}).get("dateTime")
    end_iso = original_event.get("end", {}).get("dateTime") # Also format end time if needed

    formatted_start_time = format_datetime_for_display(start_iso)
    # formatted_end_time = format_datetime_for_display(end_iso) # If you want to show end time

    print(f"{Style.BRIGHT}{index}. Event: {Fore.YELLOW}{title}{Style.RESET_ALL}")
    print(f"   Starts: {Fore.YELLOW}{formatted_start_time}{Style.RESET_ALL}")
    # print(f"   Ends:   {Fore.YELLOW}{formatted_end_time}{Style.RESET_ALL}")
    print(f"   {Fore.WHITE}LLM Note: {event_data.get('summary_llm', 'N/A')}{Style.RESET_ALL}")

    actions = event_data.get('suggested_actions', [])
    if actions:
        print(f"   {Fore.GREEN}Suggested Actions:{Style.RESET_ALL}")
        for i, action_text in enumerate(actions):
            print(f"     {Style.BRIGHT}{Fore.GREEN}({chr(97 + i)}){Style.RESET_ALL} {action_text}")
    print("-" * 10)

# user_interface.py
def display_processed_data_and_get_action(
    important_emails_llm: List[Dict[str, Any]],
    processed_events_llm: List[Dict[str, Any]],
    first_time_display: bool = True # New parameter
) -> Optional[Tuple[str, int, int, str]]: # type, item_idx, action_idx, raw_user_choice
    """
    Displays summarized emails and events, then prompts for action.
    Returns:
        - ("email", email_idx, action_idx, raw_choice)
        - ("event", event_idx, action_idx, raw_choice)
        - ("done", -1, -1, "d") if user wants to finish with this set of items
        - ("quit_assistant", -1, -1, "q") if user wants to quit the whole assistant
        - None if no actionable items or invalid input format after trying
    """
    actionable_items_present = False
    if first_time_display: # Only print headers and full lists the first time in a cycle
        if important_emails_llm:
            print_header("Important Emails")
            for i, email_data in enumerate(important_emails_llm):
                display_email_summary(i + 1, email_data)
            actionable_items_present = True
        else:
            print(f"{Fore.GREEN}No new important emails requiring immediate attention.{Style.RESET_ALL}")

        if processed_events_llm:
            print_header("Upcoming Calendar Events")
            for i, event_data in enumerate(processed_events_llm):
                if event_data.get('suggested_actions'):
                    display_calendar_event_summary(len(important_emails_llm) + i + 1, event_data)
                    actionable_items_present = True
        else:
            print(f"{Fore.GREEN}No upcoming events with specific suggestions.{Style.RESET_ALL}")

        if not actionable_items_present:
            print(f"\n{Fore.GREEN}All caught up for now!{Style.RESET_ALL}")
            return None
    elif not important_emails_llm and not any(e.get('suggested_actions') for e in processed_events_llm):
        # If, after an action, there are no more items (e.g., last item was deleted)
        print(f"\n{Fore.GREEN}No more actionable items in this cycle.{Style.RESET_ALL}")
        return ("done", -1, -1, "d")


    print(f"\n{Style.BRIGHT}Choose an action (e.g., '1a'), or type:"
          f"\n  '{Fore.YELLOW}d{Style.RESET_ALL}{Style.BRIGHT}' when done with actions for this cycle,"
          f"\n  '{Fore.YELLOW}r{Style.RESET_ALL}{Style.BRIGHT}' to refresh/re-display items,"
          f"\n  '{Fore.YELLOW}q{Style.RESET_ALL}{Style.BRIGHT}' to quit assistant:{Style.RESET_ALL}")

    user_choice_str = input(f"{Fore.CYAN}> {Style.RESET_ALL}").strip().lower()

    if user_choice_str in ['d', 'done']:
        return "done", -1, -1, user_choice_str
    if user_choice_str in ['q', 'quit']:
        return "quit_assistant", -1, -1, user_choice_str
    if user_choice_str in ['r', 'refresh', 'redisplay']:
        return "redisplay", -1, -1, user_choice_str


    if len(user_choice_str) >= 2 and user_choice_str[:-1].isdigit() and user_choice_str[-1].isalpha():
        item_num_chosen = int(user_choice_str[:-1])
        action_char_chosen = user_choice_str[-1]
        action_idx_chosen = ord(action_char_chosen) - ord('a')

        if 1 <= item_num_chosen <= len(important_emails_llm):
            item_type = "email"
            actual_item_idx = item_num_chosen - 1
            if 0 <= action_idx_chosen < len(important_emails_llm[actual_item_idx].get('suggested_actions', [])):
                return item_type, actual_item_idx, action_idx_chosen, user_choice_str
            else:
                print(f"{Fore.RED}Invalid action '{action_char_chosen}' for email {item_num_chosen}.{Style.RESET_ALL}")
        # ... (event logic for choosing action, same as before, ensure it also returns user_choice_str)
        elif len(important_emails_llm) < item_num_chosen <= (len(important_emails_llm) + len(processed_events_llm)):
            # Adjusting to correctly index into processed_events_llm which might have non-actionable items filtered out by display
            # This needs care: the numbering presented to user must map back correctly.
            # For simplicity, let's assume processed_events_llm only contains actionable events for display indexing.
            # This part needs careful alignment with how events are numbered and stored if some are filtered from display.
            # For now, assuming display_calendar_event_summary was called for all in processed_events_llm that had actions.

            # Let's filter processed_events_llm to only those that were displayed (had actions)
            displayable_events = [e for e in processed_events_llm if e.get('suggested_actions')]

            if len(important_emails_llm) < item_num_chosen <= (len(important_emails_llm) + len(displayable_events)):
                item_type = "event"
                # User's item_num_chosen is 1-based and global.
                # actual_item_idx is 0-based for the displayable_events list.
                actual_item_idx_in_displayable = item_num_chosen - 1 - len(important_emails_llm)

                if 0 <= actual_item_idx_in_displayable < len(displayable_events):
                    # We need to find this event in the original processed_events_llm list to get its original index
                    # This is tricky if items were filtered. A safer way is to pass back the actual item or its original index.
                    # For now, let's assume a direct mapping for simplicity, will need refinement if filtering display.
                    # Simpler: find the original event data corresponding to this displayable event
                    chosen_displayable_event = displayable_events[actual_item_idx_in_displayable]
                    original_event_idx = -1
                    for idx, orig_event in enumerate(processed_events_llm):
                        # Assuming event IDs are unique and present from LLM
                        if orig_event.get("original_event_data",{}).get("id") == chosen_displayable_event.get("original_event_data",{}).get("id"):
                            original_event_idx = idx
                            break

                    if original_event_idx != -1 and 0 <= action_idx_chosen < len(chosen_displayable_event.get('suggested_actions', [])):
                         return item_type, original_event_idx, action_idx_chosen, user_choice_str # Return original_event_idx
                    else:
                        print(f"{Fore.RED}Invalid action '{action_char_chosen}' for event {item_num_chosen} or event ID mismatch.{Style.RESET_ALL}")
                else:
                    print(f"{Fore.RED}Invalid selection for event {item_num_chosen}.{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}Invalid item number '{item_num_chosen}'.{Style.RESET_ALL}")
    else:
        if user_choice_str:
            print(f"{Fore.RED}Invalid input format. Use item number then action letter (e.g., '1a').{Style.RESET_ALL}")

    return None # Invalid input or no action

def get_send_edit_cancel_confirmation(draft_text: str, service_name: str = "email") -> str:
    """
    Displays a draft and asks for confirmation: Send, Edit, or Cancel.
    Returns "send", "edit", or "cancel".
    PM: Clear choices, default to cancel if input is ambiguous to be safe.
    """
    print_header(f"Draft {service_name.capitalize()}")
    print(f"{Fore.WHITE}{draft_text}{Style.RESET_ALL}")

    while True:
        choice = input(
            f"{Fore.CYAN}Action: ({Style.BRIGHT}{Fore.GREEN}S{Style.RESET_ALL}{Fore.CYAN})end, "
            f"({Style.BRIGHT}{Fore.YELLOW}E{Style.RESET_ALL}{Fore.CYAN})dit, "
            f"({Style.BRIGHT}{Fore.RED}C{Style.RESET_ALL}{Fore.CYAN})ancel? {Style.RESET_ALL}"
        ).strip().lower()
        if choice in ['s', 'send']:
            return "send_reply"
        if choice in ['e', 'edit']:
            return "edit"
        if choice in ['c', 'cancel', '']: # Empty input defaults to cancel
            return "cancel"
        print(f"{Fore.RED}Invalid choice. Please enter S, E, or C.{Style.RESET_ALL}")

def get_event_update_choices(original_event_summary: str, original_event_details: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    print_header(f"Update Event: {original_event_summary[:50]}...")
    updates: Dict[str, Any] = {} # Ensure updates is always a dict

    # Extract original start and timezone for later use if needed
    original_start_iso = original_event_details.get("start", {}).get("dateTime")
    original_event_timezone = original_event_details.get("start", {}).get("timeZone") # Google API often provides this

    fields_to_update = {
        "1": {"name": "Title (Summary)", "key": "summary", "type": "str"},
        "2": {"name": "Start Datetime (YYYY-MM-DDTHH:MM:SS, local to event)", "key": "start_datetime", "type": "datetime_str"},
        "3": {"name": "Duration (Hours, Minutes)", "key": "duration", "type": "duration"},
        "4": {"name": "Description", "key": "description", "type": "str"},
        "5": {"name": "Location", "key": "location", "type": "str"},
        "6": {"name": "Attendees (comma-separated emails)", "key": "attendees", "type": "email_list"},
        "7": {"name": "Google Meet Link (Add/Ensure)", "key": "create_meeting_room", "type": "bool_true"},
    }

    while True:
        print("\nWhat would you like to update?")
        for key_choice, val_info in fields_to_update.items():
            current_val_indicator = ""
            if val_info['key'] in updates:
                current_val_indicator = f" (current: {Fore.YELLOW}{updates[val_info['key']]}{Style.RESET_ALL})"
            elif val_info['key'] == "create_meeting_room" and updates.get("create_meeting_room") is True: # Specifically for bool_true
                 current_val_indicator = f" (current: {Fore.YELLOW}True{Style.RESET_ALL})"
            elif val_info['key'] == "duration" and ("event_duration_hour" in updates or "event_duration_minutes" in updates):
                 current_val_indicator = f" (current: {Fore.YELLOW}{updates.get('event_duration_hour',0)}h {updates.get('event_duration_minutes',0)}m{Style.RESET_ALL})"
            print(f"  {Style.BRIGHT}{key_choice}{Style.RESET_ALL}. {val_info['name']}{current_val_indicator}")

        print(f"  {Style.BRIGHT}s{Style.RESET_ALL}. Save changes and proceed to update")
        print(f"  {Style.BRIGHT}c{Style.RESET_ALL}. Cancel update")

        choice = get_user_input("Choose field to edit, 's' to save, or 'c' to cancel").lower()

        if choice == 'c': return None
        if choice == 's':
            if not updates:
                print(f"{Fore.YELLOW}No changes made. Update cancelled.{Style.RESET_ALL}")
                return None

            # If ANY update is being made, ensure start_datetime, timezone, and duration are present
            # because GOOGLECALENDAR_UPDATE_EVENT marks start_datetime as REQUIRED.
            needs_base_timing_info = bool(updates)

            if needs_base_timing_info:
                # Ensure start_datetime is present
                if "start_datetime" not in updates:
                    if original_start_iso:
                        try:
                            dt_obj = datetime.fromisoformat(original_start_iso)
                            updates["start_datetime"] = dt_obj.strftime("%Y-%m-%dT%H:%M:%S") # Naive
                        except ValueError:
                            print(f"{Fore.RED}Original event start time '{original_start_iso}' is invalid. Update cannot proceed without a valid start time.{Style.RESET_ALL}")
                            return None
                    else:
                        print(f"{Fore.RED}Error: Start Datetime is required for any update and original could not be found. Update cannot proceed.{Style.RESET_ALL}")
                        return None

                # Ensure timezone is present (goes with start_datetime)
                if "timezone" not in updates:
                    if original_event_timezone: # From event.start.timeZone
                        updates["timezone"] = original_event_timezone
                    elif original_start_iso: # Try to derive from full ISO string's offset if event.start.timeZone missing
                        try:
                            dt_obj_for_tz = datetime.fromisoformat(original_start_iso)
                            if dt_obj_for_tz.tzinfo:
                                # This gets offset like +05:30. Google API/Composio likely needs IANA.
                                # For robustness, if original_event_timezone (IANA) isn't there, better to prompt or use a default.
                                updates["timezone"] = get_user_input(f"Enter timezone for start time '{updates['start_datetime']}' (e.g., Asia/Kolkata)", default="UTC")
                            else: # Naive original, prompt for TZ
                                 updates["timezone"] = get_user_input(f"Enter timezone for start time '{updates['start_datetime']}' (e.g., Asia/Kolkata)", default="UTC")
                        except ValueError: # Fallback to prompt
                            updates["timezone"] = get_user_input(f"Enter timezone for start time '{updates['start_datetime']}' (e.g., Asia/Kolkata)", default="UTC")
                    else: # Fallback to prompt
                        updates["timezone"] = get_user_input(f"Enter timezone for start time '{updates.get('start_datetime', 'UNKNOWN')}' (e.g., Asia/Kolkata)", default="UTC")

                # Ensure duration fields are present
                if "event_duration_hour" not in updates and "event_duration_minutes" not in updates:
                    original_end_iso = original_event_details.get("end", {}).get("dateTime")
                    if original_start_iso and original_end_iso:
                        try:
                            orig_start_dt = datetime.fromisoformat(original_start_iso)
                            orig_end_dt = datetime.fromisoformat(original_end_iso)
                            duration_delta = orig_end_dt - orig_start_dt
                            total_minutes = max(0, int(duration_delta.total_seconds() / 60))
                            updates["event_duration_hour"] = total_minutes // 60
                            updates["event_duration_minutes"] = total_minutes % 60
                        except ValueError:
                            updates["event_duration_hour"] = 0
                            updates["event_duration_minutes"] = 30
                    else:
                        updates["event_duration_hour"] = 0
                        updates["event_duration_minutes"] = 30
            if "summary" not in updates and original_event_details.get("summary"):
                updates["summary"] = original_event_details.get("summary")
                print(f"   (DEBUG: Auto-added original summary '{updates['summary']}' to prevent loss)")

            return updates

        if choice in fields_to_update:
            field_info = fields_to_update[choice]
            field_key = field_info["key"]
            field_type = field_info["type"]

            if field_type == "str":
                updates[field_key] = get_user_input(f"Enter new {field_info['name']}")
            elif field_type == "datetime_str":
                # PM: Add validation for YYYY-MM-DDTHH:MM:SS format
                new_val = get_user_input(f"Enter new {field_info['name']} (YYYY-MM-DDTHH:MM:SS)")
                # Basic validation example (can be more robust)
                try:
                    datetime.strptime(new_val, "%Y-%m-%dT%H:%M:%S")
                    updates[field_key] = new_val
                    # If start_datetime is naive, we should also prompt for timezone if not already set
                    if "timezone" not in updates:
                        tz = get_user_input("Enter Timezone (e.g., Asia/Kolkata, or press Enter for UTC if datetime has Z/offset)", default="UTC")
                        if tz != "UTC" or "Z" not in new_val or "+" not in new_val: # Only set if needed
                             updates["timezone"] = tz if tz else "UTC" # Default to UTC if empty
                except ValueError:
                    print(f"{Fore.RED}Invalid datetime format. Please use YYYY-MM-DDTHH:MM:SS{Style.RESET_ALL}")
            elif field_type == "duration":
                try:
                    h = int(get_user_input("Enter new duration hours (0-23)", default="0"))
                    m = int(get_user_input("Enter new duration minutes (0-59)", default="30"))
                    if 0 <= h <= 23 and 0 <= m <= 59:
                        updates["event_duration_hour"] = h
                        updates["event_duration_minutes"] = m
                    else:
                        print(f"{Fore.RED}Invalid duration values.{Style.RESET_ALL}")
                except ValueError:
                    print(f"{Fore.RED}Duration must be numbers.{Style.RESET_ALL}")
            elif field_type == "email_list": # For attendees
                current_attendees_str = ", ".join(updates.get(field_key, [])) # Current is now list of strings
                emails_str = get_user_input(f"Enter new {field_info['name']} (comma-separated emails)", default=current_attendees_str)
                attendee_email_strings = [e.strip() for e in emails_str.split(',') if e.strip() and "@" in e]
                updates[field_key] = attendee_email_strings # Store as list of strings
                print(f"   (DEBUG: Attendees to be sent as list of strings: {updates[field_key]})")
            elif field_type == "bool_true":
                print(f"DEBUG: Prompting for {field_info['name']}")
                if get_yes_no_input(f"Set {field_info['name']} to True (create/ensure Meet link)?", default_yes=updates.get(field_key, True)):
                    updates[field_key] = True
                    print(f"   (DEBUG: {field_key} set to True in updates dict)")
                else:
                    if field_key in updates:
                        del updates[field_key]
                    print(f"   (DEBUG: {field_key} will not be sent or set to false by default by API)")

        else:
            print(f"{Fore.RED}Invalid choice.{Style.RESET_ALL}")

if __name__ == "__main__":
    print("--- Testing user_interface.py ---")

    mock_important_emails = [
        {"original_email_data": {"sender": "boss@example.com", "subject": "Urgent Meeting Q3"}, "summary": "Boss wants an urgent meeting about Q3.", "suggested_actions": ["Draft 'Confirm Availability'", "Check calendar for conflicts"]},
        {"original_email_data": {"sender": "client@example.com", "subject": "Proposal Feedback"}, "summary": "Client gave feedback on the proposal, mostly positive.", "suggested_actions": ["Draft 'Thank You & Acknowledge'", "Review feedback document"]}
    ]
    mock_processed_events = [
        {"original_event_data": {"summary": "Team Sync", "start": {"dateTime": "2025-06-01T10:00:00"}}, "summary_llm": "Regular team sync meeting.", "suggested_actions": ["Prepare talking points"]},
        {"original_event_data": {"summary": "1:1 with Alice", "start": {"dateTime": "2025-06-01T14:00:00"}}, "summary_llm": "Catch up with Alice.", "suggested_actions": ["Review Alice's recent work", "Discuss project X blocker"]}
    ]

    action_choice = display_processed_data_and_get_action(mock_important_emails, mock_processed_events)
    print(f"\nUser chose: {action_choice}")

    if action_choice and action_choice[0] not in ["skip", "quit"]:
        item_type, item_idx, action_idx = action_choice
        if item_type == "email":
            chosen_item = mock_important_emails[item_idx]
            chosen_action_text = chosen_item['suggested_actions'][action_idx]
            print(f"Interpreted as: Act on {item_type} '{chosen_item['original_email_data']['subject']}' with action '{chosen_action_text}'")
        elif item_type == "event":
            chosen_item = mock_processed_events[item_idx]
            chosen_action_text = chosen_item['suggested_actions'][action_idx]
            print(f"Interpreted as: Act on {item_type} '{chosen_item['original_event_data']['summary']}' with action '{chosen_action_text}'")


    print("\nTesting draft confirmation:")
    draft = "Hello,\n\nThis is a sample draft email.\n\nBest,\nAssistant"
    confirmation = confirmation = get_send_edit_cancel_confirmation(draft)
    print(f"User confirmation for draft: {confirmation}")

    print("\n--- Test complete ---")
