# user_interface.py
import sys
from typing import List, Dict, Any, Optional, Tuple
from colorama import Fore, Style, init as colorama_init
from datetime import datetime, timezone # Ensure datetime is imported

# PM Consideration: Initialize colorama once for cross-platform color support.
colorama_init(autoreset=True)

# --- Input Functions ---
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
    style_prefix = f"{Style.BRIGHT}{Fore.RED}" if destructive else f"{Fore.YELLOW}"
    prompt = f"{style_prefix}{prompt_message}{Style.RESET_ALL} ({Fore.GREEN}y{Style.RESET_ALL}/{Fore.RED}N{Style.RESET_ALL})? : "
    while True:
        response = input(prompt).strip().lower()
        if not response:
            return False
        if response in ['y', 'yes']:
            return True
        if response in ['n', 'no']:
            return False
        print(f"{Fore.RED}Invalid input. Please enter 'y' or 'n'.{Style.RESET_ALL}")

def display_email_summary(index: int, email_data: Dict[str, Any]):
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
            print(f"     {Style.BRIGHT}{Fore.GREEN}({chr(97 + i)}){Style.RESET_ALL} {action_text}")
    print("-" * 10)

def format_datetime_for_display(iso_datetime_str: Optional[str]) -> str:
    if not iso_datetime_str:
        return "N/A"
    try:
        dt_object = datetime.fromisoformat(iso_datetime_str)
        dt_object_local = dt_object.astimezone()
        return dt_object_local.strftime("%b %d, %I:%M %p %Z")
    except ValueError:
        return iso_datetime_str

def display_calendar_event_summary(index: int, event_data: Dict[str, Any]):
    original_event = event_data.get("original_event_data", {})
    title = original_event.get("summary", "No Title")
    start_iso = original_event.get("start", {}).get("dateTime")
    formatted_start_time = format_datetime_for_display(start_iso)

    print(f"{Style.BRIGHT}{index}. Event: {Fore.YELLOW}{title}{Style.RESET_ALL}")
    print(f"   Starts: {Fore.YELLOW}{formatted_start_time}{Style.RESET_ALL}")
    print(f"   {Fore.WHITE}LLM Note: {event_data.get('summary_llm', 'N/A')}{Style.RESET_ALL}")

    actions = event_data.get('suggested_actions', [])
    if actions:
        print(f"   {Fore.GREEN}Suggested Actions:{Style.RESET_ALL}")
        for i, action_text in enumerate(actions):
            print(f"     {Style.BRIGHT}{Fore.GREEN}({chr(97 + i)}){Style.RESET_ALL} {action_text}")
    print("-" * 10)

def display_processed_data_and_get_action(
    actionable_emails: List[Dict[str, Any]],
    actionable_events: List[Dict[str, Any]],
    first_time_display: bool = True,
    run_mode: str = "normal" # Added to control NLI prompt
) -> Optional[Tuple[str, int, int, str]]:

    actionable_items_present_in_call = False
    if first_time_display:
        if actionable_emails:
            print_header("Important Emails")
            for i, email_data in enumerate(actionable_emails):
                display_email_summary(i + 1, email_data)
            actionable_items_present_in_call = True
        else:
            print(f"{Fore.GREEN}No new important emails requiring immediate attention.{Style.RESET_ALL}")

        if actionable_events:
            print_header("Upcoming Calendar Events with Actions")
            for i, event_data in enumerate(actionable_events):
                display_calendar_event_summary(len(actionable_emails) + i + 1, event_data)
            actionable_items_present_in_call = True
        else:
            print(f"{Fore.GREEN}No upcoming events with specific suggestions.{Style.RESET_ALL}")

        if not actionable_items_present_in_call:
            print(f"\n{Fore.GREEN}All caught up! No items requiring immediate action choices.{Style.RESET_ALL}")
            if run_mode == "normal": # If NLI-first, but no actionables, user might still want to chat
                return ("no_actionables_chat_nli", -1, -1, "chat_nli")
            return ("done", -1, -1, "d")
    elif not actionable_emails and not actionable_events:
        print(f"\n{Fore.GREEN}No more actionable items in this cycle.{Style.RESET_ALL}")
        if run_mode == "normal":
            return ("no_actionables_chat_nli", -1, -1, "chat_nli")
        return ("done", -1, -1, "d")

    prompt_message_parts = [
        f"\n{Style.BRIGHT}Choose an action (e.g., '1a'), or type:",
        f"  '{Fore.YELLOW}d{Style.RESET_ALL}{Style.BRIGHT}' when done with actions for this cycle,",
        f"  '{Fore.YELLOW}r{Style.RESET_ALL}{Style.BRIGHT}' to refresh/re-display items,"
    ]
    if run_mode == "from_notification": # If started from notification, actionables are default
        prompt_message_parts.append(f"  '{Fore.YELLOW}c{Style.RESET_ALL}{Style.BRIGHT}' to chat with the assistant (NLI),")

    prompt_message_parts.append(f"  '{Fore.YELLOW}q{Style.RESET_ALL}{Style.BRIGHT}' to quit assistant:{Style.RESET_ALL}")
    print("\n".join(prompt_message_parts))

    user_choice_str = input(f"{Fore.CYAN}> {Style.RESET_ALL}").strip().lower()

    if user_choice_str in ['d', 'done']:
        return "done", -1, -1, user_choice_str
    if user_choice_str in ['q', 'quit']:
        return "quit_assistant", -1, -1, user_choice_str
    if user_choice_str in ['r', 'refresh', 'redisplay']:
        return "redisplay", -1, -1, user_choice_str

    if run_mode == "from_notification" and user_choice_str in ['c', 'chat']:
        return "chat_nli", -1, -1, user_choice_str

    if len(user_choice_str) >= 2 and user_choice_str[:-1].isdigit() and user_choice_str[-1].isalpha():
        item_num_chosen = int(user_choice_str[:-1])
        action_char_chosen = user_choice_str[-1]
        action_idx_chosen = ord(action_char_chosen) - ord('a')

        if 1 <= item_num_chosen <= len(actionable_emails):
            item_type = "email"
            actual_item_idx = item_num_chosen - 1
            if 0 <= action_idx_chosen < len(actionable_emails[actual_item_idx].get('suggested_actions', [])):
                return item_type, actual_item_idx, action_idx_chosen, user_choice_str
            else:
                print(f"{Fore.RED}Invalid action '{action_char_chosen}' for email {item_num_chosen}.{Style.RESET_ALL}")

        elif len(actionable_emails) < item_num_chosen <= (len(actionable_emails) + len(actionable_events)):
            item_type = "event"
            actual_item_idx = item_num_chosen - 1 - len(actionable_emails)
            if 0 <= actual_item_idx < len(actionable_events) and \
               0 <= action_idx_chosen < len(actionable_events[actual_item_idx].get('suggested_actions', [])):
                return item_type, actual_item_idx, action_idx_chosen, user_choice_str
            else:
                print(f"{Fore.RED}Invalid action '{action_char_chosen}' for event {item_num_chosen}.{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}Invalid item number '{item_num_chosen}'.{Style.RESET_ALL}")
    else:
        if user_choice_str: # If user typed something but it wasn't a recognized command
             # If in "normal" run mode (NLI first), any unrecognized input here could be an NLI command
            if run_mode == "normal":
                return "nli_command_from_actionable_menu", -1, -1, user_choice_str
            else:
                print(f"{Fore.RED}Invalid input format. Use item number then action letter (e.g., '1a').{Style.RESET_ALL}")
    return None

# --- NLI Chat Functions ---
def get_nli_chat_input(run_mode: str = "normal") -> Tuple[str, str]:
    """
    Gets NLI input from the user.
    Returns a tuple: (command_type, user_text_input)
    command_type can be "nli_query", "show_actionables", "quit_assistant".
    """
    prompt_message_parts = [
        f"\n{Style.BRIGHT}MCliPPy NLI Chat{Style.RESET_ALL}",
        "Type your command or question. Special commands:",
    ]
    if run_mode == "normal": # NLI is default, so option to see actionables
        prompt_message_parts.append(f"  '{Fore.YELLOW}/actionables{Style.RESET_ALL}' or '{Fore.YELLOW}/a{Style.RESET_ALL}' to view quick actions list.")
    else: # Actionables was default, so option to go back
        prompt_message_parts.append(f"  '{Fore.YELLOW}/done{Style.RESET_ALL}' or '{Fore.YELLOW}/d{Style.RESET_ALL}' to return to quick actions list.")

    prompt_message_parts.append(f"  '{Fore.YELLOW}/quit{Style.RESET_ALL}' or '{Fore.YELLOW}/q{Style.RESET_ALL}' to quit assistant.")

    print("\n".join(prompt_message_parts))
    user_input = input(f"{Fore.CYAN}NLI> {Style.RESET_ALL}").strip()

    if user_input.lower() in ["/quit", "/q"]:
        return "quit_assistant", user_input

    if run_mode == "normal" and user_input.lower() in ["/actionables", "/a", "/actions"]:
        return "show_actionables", user_input
    elif run_mode == "from_notification" and user_input.lower() in ["/done", "/d", "/back"]:
        return "done_chatting", user_input # Signal to return to actionables loop

    return "nli_query", user_input


def display_nli_llm_response(response_text: str, source: str = "MCliPPy"):
    """Displays the LLM's final text response in NLI mode."""
    print(f"\n{Style.BRIGHT}{Fore.GREEN}{source}:{Style.RESET_ALL} {response_text}")

def display_tool_confirmation_prompt(tool_name: str, tool_args: Dict[str, Any]) -> bool:
    """
    Displays the tool and its arguments and asks for user confirmation.
    Returns True if confirmed, False otherwise.
    """
    print_header("Confirm Action")
    print(f"{Fore.YELLOW}Assistant suggests executing the following action:{Style.RESET_ALL}")
    print(f"  Tool: {Style.BRIGHT}{tool_name}{Style.RESET_ALL}")
    print(f"  With Parameters:")
    for key, value in tool_args.items():
        print(f"    {key}: {Fore.CYAN}{value}{Style.RESET_ALL}")

    return get_yes_no_input("Do you want to proceed with this action?", default_yes=True)


def get_send_edit_cancel_confirmation(draft_text: str, service_name: str = "email") -> str:
    print_header(f"Draft {service_name.capitalize()}")
    print(f"{Fore.WHITE}{draft_text}{Style.RESET_ALL}")
    while True:
        choice = input(
            f"{Fore.CYAN}Action: ({Style.BRIGHT}{Fore.GREEN}S{Style.RESET_ALL}{Fore.CYAN})end, "
            f"({Style.BRIGHT}{Fore.YELLOW}E{Style.RESET_ALL}{Fore.CYAN})dit, "
            f"({Style.BRIGHT}{Fore.RED}C{Style.RESET_ALL}{Fore.CYAN})ancel? {Style.RESET_ALL}"
        ).strip().lower()
        if choice in ['s', 'send']:
            return "send_reply" # Should be generic, like "send_action"
        if choice in ['e', 'edit']:
            return "edit"
        if choice in ['c', 'cancel', '']:
            return "cancel"
        print(f"{Fore.RED}Invalid choice. Please enter S, E, or C.{Style.RESET_ALL}")

def display_free_slots(free_slots_list: List[Dict[str, str]], for_date_str: str):
    print_header(f"Available Free Slots for {for_date_str}")
    if not free_slots_list:
        print(f"{Fore.YELLOW}No free slots found for the specified duration and date.{Style.RESET_ALL}")
        return
    for i, slot in enumerate(free_slots_list):
        start_display = format_datetime_for_display(slot.get("start"))
        end_display = format_datetime_for_display(slot.get("end"))
        print(f"  {Style.BRIGHT}{i+1}.{Style.RESET_ALL} {Fore.GREEN}{start_display}{Style.RESET_ALL} to {Fore.GREEN}{end_display}{Style.RESET_ALL}")
    print("-" * 10)

def get_event_update_choices(original_event_summary: str, original_event_details: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    print_header(f"Update Event: {original_event_summary[:50]}...")
    updates: Dict[str, Any] = {}
    original_start_iso = original_event_details.get("start", {}).get("dateTime")
    original_event_timezone = original_event_details.get("start", {}).get("timeZone")

    fields_to_update = {
        "1": {"name": "Title (Summary)", "key": "summary", "type": "str"},
        "2": {"name": "Start Datetime (YYYY-MM-DDTHH:MM:SS, local to event)", "key": "start_datetime", "type": "datetime_str"},
        "3": {"name": "Duration (Hours, Minutes)", "key": "duration", "type": "duration"},
        "4": {"name": "Description", "key": "description", "type": "str"},
        "5": {"name": "Location", "key": "location", "type": "str"},
        "6": {"name": "Attendees (comma-separated emails)", "key": "attendees", "type": "email_list_str"},
        "7": {"name": "Google Meet Link (Add/Ensure)", "key": "create_meeting_room", "type": "bool_true"},
        "f": {"name": "Find & Display Free Slots", "key": "find_free_slots_trigger", "type": "action_trigger"}
    }

    while True:
        print("\nWhat would you like to update?")
        for key_choice, val_info in fields_to_update.items():
            current_val_indicator = ""
            if val_info['key'] in updates:
                current_val_indicator = f" (current: {Fore.YELLOW}{updates[val_info['key']]}{Style.RESET_ALL})"
            elif val_info['key'] == "create_meeting_room" and updates.get("create_meeting_room") is True:
                 current_val_indicator = f" (current: {Fore.YELLOW}True{Style.RESET_ALL})"
            elif val_info['key'] == "duration" and ("event_duration_hour" in updates or "event_duration_minutes" in updates):
                 current_val_indicator = f" (current: {Fore.YELLOW}{updates.get('event_duration_hour',0)}h {updates.get('event_duration_minutes',0)}m{Style.RESET_ALL})"
            if val_info["type"] == "action_trigger":
                print(f"  {Style.BRIGHT}{key_choice}{Style.RESET_ALL}. {val_info['name']}")
            else:
                print(f"  {Style.BRIGHT}{key_choice}{Style.RESET_ALL}. {val_info['name']}{current_val_indicator}")

        print(f"  {Style.BRIGHT}s{Style.RESET_ALL}. Save changes and proceed to update")
        print(f"  {Style.BRIGHT}c{Style.RESET_ALL}. Cancel update")

        choice = get_user_input("Choose field to edit, 's' to save, or 'c' to cancel").lower()

        if choice == 'c': return None
        if choice == 's':
            if not updates:
                print(f"{Fore.YELLOW}No changes made. Update cancelled.{Style.RESET_ALL}")
                return None
            needs_base_timing_info = ("start_datetime" in updates or \
                                  "event_duration_hour" in updates or \
                                  "event_duration_minutes" in updates or \
                                  bool(updates))
            if needs_base_timing_info:
                if "start_datetime" not in updates:
                    if original_start_iso:
                        try:
                            dt_obj = datetime.fromisoformat(original_start_iso)
                            updates["start_datetime"] = dt_obj.strftime("%Y-%m-%dT%H:%M:%S")
                        except ValueError:
                            print(f"{Fore.RED}Original event start time '{original_start_iso}' is invalid. Update cannot proceed without a valid start time.{Style.RESET_ALL}")
                            return None
                    else:
                        print(f"{Fore.RED}Error: Start Datetime is required for any update and original could not be found. Update cannot proceed.{Style.RESET_ALL}")
                        return None
                if "timezone" not in updates:
                    if original_event_timezone:
                        updates["timezone"] = original_event_timezone
                    elif original_start_iso:
                         try:
                            dt_obj_for_tz = datetime.fromisoformat(original_start_iso)
                            if dt_obj_for_tz.tzinfo:
                                updates["timezone"] = get_user_input(f"Enter timezone for start time '{updates['start_datetime']}' (e.g., Asia/Kolkata)", default="UTC")
                            else:
                                 updates["timezone"] = get_user_input(f"Enter timezone for start time '{updates['start_datetime']}' (e.g., Asia/Kolkata)", default="UTC")
                         except ValueError:
                            updates["timezone"] = get_user_input(f"Enter timezone for start time '{updates.get('start_datetime', 'UNKNOWN')}' (e.g., Asia/Kolkata)", default="UTC")
                    else:
                        updates["timezone"] = get_user_input(f"Enter timezone for start time '{updates.get('start_datetime', 'UNKNOWN')}' (e.g., Asia/Kolkata)", default="UTC")
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
                            updates["event_duration_hour"] = 0; updates["event_duration_minutes"] = 30
                    else:
                        updates["event_duration_hour"] = 0; updates["event_duration_minutes"] = 30
            if "summary" not in updates and original_event_details.get("summary"):
                updates["summary"] = original_event_details.get("summary")
            return updates

        if choice in fields_to_update:
            field_info = fields_to_update[choice]
            field_key = field_info["key"]
            field_type = field_info["type"]

            if field_type == "action_trigger" and field_key == "find_free_slots_trigger":
                return {"trigger_action": "find_free_slots"}
            if field_type == "str":
                updates[field_key] = get_user_input(f"Enter new {field_info['name']}")
            elif field_type == "datetime_str":
                new_val = get_user_input(f"Enter new {field_info['name']} (YYYY-MM-DDTHH:MM:SS)")
                try:
                    datetime.strptime(new_val, "%Y-%m-%dT%H:%M:%S")
                    updates[field_key] = new_val
                    if "timezone" not in updates:
                        tz = get_user_input("Enter Timezone (e.g., Asia/Kolkata, or press Enter for UTC if datetime has Z/offset)", default="UTC")
                        if tz != "UTC" or "Z" not in new_val or "+" not in new_val:
                             updates["timezone"] = tz if tz else "UTC"
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
            elif field_type == "email_list_str":
                current_attendees_str = ", ".join(updates.get(field_key, []))
                emails_str = get_user_input(f"Enter new {field_info['name']} (comma-separated emails)", default=current_attendees_str)
                attendee_email_strings = [e.strip() for e in emails_str.split(',') if e.strip() and "@" in e]
                updates[field_key] = attendee_email_strings
            elif field_type == "bool_true":
                if get_yes_no_input(f"Set {field_info['name']} to True (create/ensure Meet link)?", default_yes=updates.get(field_key, True)):
                    updates[field_key] = True
                else:
                    if field_key in updates:
                        del updates[field_key]
        else:
            print(f"{Fore.RED}Invalid choice.{Style.RESET_ALL}")

def get_event_creation_confirmation_and_edits(
    llm_parsed_details: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    print_header("Create New Calendar Event")
    current_details = llm_parsed_details.copy()

    fields_for_creation = {
        "1": {"name": "Title (Summary)", "key": "summary", "type": "str"},
        "2": {"name": "Start Datetime (YYYY-MM-DDTHH:MM:SS)", "key": "start_datetime", "type": "datetime_str"},
        "3": {"name": "Timezone (e.g., Asia/Kolkata)", "key": "timezone", "type": "str"},
        "4": {"name": "Duration (Hours, Minutes)", "key": "duration", "type": "duration"},
        "5": {"name": "Description", "key": "description", "type": "str_optional"},
        "6": {"name": "Location", "key": "location", "type": "str_optional"},
        "7": {"name": "Attendees (comma-separated emails)", "key": "attendees", "type": "email_list_str"},
        "8": {"name": "Add Google Meet Link", "key": "create_meeting_room", "type": "bool_choice"},
        "f": {"name": "Find & Suggest Free Slots for Start Time", "key": "find_free_slots_trigger", "type": "action_trigger"}
    }

    while True:
        print("\nPlease review and confirm details for the new event (or edit):")
        for key_choice, info in fields_for_creation.items():
            val = current_details.get(info["key"])
            if info["key"] == "duration":
                h = current_details.get("event_duration_hour", 0)
                m = current_details.get("event_duration_minutes", 30)
                val_display = f"{h}h {m}m"
            elif isinstance(val, list):
                val_display = ", ".join(val) if val else "None"
            elif isinstance(val, bool):
                val_display = str(val)
            else:
                val_display = val if val is not None else "Not set"
            print(f"  {Style.BRIGHT}{key_choice}{Style.RESET_ALL}. {info['name']}: {Fore.YELLOW}{val_display}{Style.RESET_ALL}")

        print(f"\n  {Style.BRIGHT}s{Style.RESET_ALL}. Save and Create Event")
        print(f"  {Style.BRIGHT}c{Style.RESET_ALL}. Cancel Creation")

        choice = get_user_input("Choose field to edit, 's' to save, or 'c' to cancel").lower()

        if choice == 'c': return None
        if choice == 's':
            if not all(k in current_details for k in ["summary", "start_datetime", "timezone"]):
                print(f"{Fore.RED}Error: Title, Start Datetime, and Timezone are required to create an event.{Style.RESET_ALL}")
                continue
            return current_details

        if choice in fields_for_creation:
            field_info = fields_for_creation[choice]
            field_key = field_info["key"]
            field_type = field_info["type"]

            if field_type == "action_trigger" and field_key == "find_free_slots_trigger":
                return {"trigger_action": "find_free_slots"}

            if field_key == "duration":
                try:
                    h = int(get_user_input("Enter duration hours (0-23)", default=str(current_details.get("event_duration_hour",0)) ))
                    m = int(get_user_input("Enter duration minutes (0-59)", default=str(current_details.get("event_duration_minutes",30)) ))
                    if 0 <= h <= 23 and 0 <= m <= 59:
                        current_details["event_duration_hour"] = h
                        current_details["event_duration_minutes"] = m
                except ValueError: print(f"{Fore.RED}Invalid duration.{Style.RESET_ALL}")
            elif field_key == "attendees":
                emails_str = get_user_input(f"Enter {field_info['name']}", default=", ".join(current_details.get(field_key,[])))
                current_details[field_key] = [e.strip() for e in emails_str.split(',') if e.strip() and "@" in e]
            elif field_key == "create_meeting_room":
                current_details[field_key] = get_yes_no_input(f"{field_info['name']}?", default_yes=current_details.get(field_key, True))
            elif field_key == "start_datetime":
                new_val = get_user_input(f"Enter new {field_info['name']} (YYYY-MM-DDTHH:MM:SS)", default=current_details.get(field_key))
                try:
                    datetime.strptime(new_val, "%Y-%m-%dT%H:%M:%S")
                    current_details[field_key] = new_val
                    if "timezone" not in current_details or not current_details["timezone"]:
                        current_details["timezone"] = get_user_input(f"Enter Timezone for this start time (e.g., Asia/Kolkata)", default=current_details.get("timezone", "Asia/Kolkata"))
                except ValueError: print(f"{Fore.RED}Invalid datetime format.{Style.RESET_ALL}")
            else: # Handles "summary", "timezone", "description", "location"
                current_details[field_key] = get_user_input(f"Enter new {field_info['name']}", default=str(current_details.get(field_key, "")))
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

    print("\nTesting actionable items display (from_notification mode):")
    action_choice = display_processed_data_and_get_action(mock_important_emails, mock_processed_events, run_mode="from_notification")
    print(f"User chose: {action_choice}")

    print("\nTesting NLI input (normal mode):")
    nli_cmd_type, nli_text = get_nli_chat_input(run_mode="normal")
    print(f"NLI command type: {nli_cmd_type}, Text: '{nli_text}'")

    print("\nTesting NLI input (from_notification mode):")
    nli_cmd_type_notif, nli_text_notif = get_nli_chat_input(run_mode="from_notification")
    print(f"NLI command type: {nli_cmd_type_notif}, Text: '{nli_text_notif}'")


    print("\nTesting draft confirmation:")
    draft = "Hello,\n\nThis is a sample draft email.\n\nBest,\nAssistant"
    confirmation = get_send_edit_cancel_confirmation(draft)
    print(f"User confirmation for draft: {confirmation}")

    print("\n--- Test complete ---")
