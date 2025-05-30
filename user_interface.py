# user_interface.py
import sys
from typing import List, Dict, Any, Optional, Tuple
from colorama import Fore, Style, init as colorama_init

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

def display_calendar_event_summary(index: int, event_data: Dict[str, Any]):
    """Displays a summary for a single calendar event."""
    original_event = event_data.get("original_event_data", {})
    title = original_event.get("summary", "No Title")
    start_time = original_event.get("start", {}).get("dateTime", "N/A")

    print(f"{Style.BRIGHT}{index}. Event: {Fore.YELLOW}{title}{Style.RESET_ALL}")
    print(f"   Starts: {Fore.YELLOW}{start_time}{Style.RESET_ALL}") # PM: Format this datetime better later
    print(f"   {Fore.WHITE}LLM Note: {event_data.get('summary_llm', 'N/A')}{Style.RESET_ALL}")

    actions = event_data.get('suggested_actions', [])
    if actions:
        print(f"   {Fore.GREEN}Suggested Actions:{Style.RESET_ALL}")
        for i, action_text in enumerate(actions):
            print(f"     {Style.BRIGHT}{Fore.GREEN}({chr(97 + i)}){Style.RESET_ALL} {action_text}")
    print("-" * 10)

def display_processed_data_and_get_action(
    important_emails_llm: List[Dict[str, Any]],
    processed_events_llm: List[Dict[str, Any]]
) -> Optional[Tuple[str, int, int]]: # type, item_index, action_index
    """
    Displays summarized emails and events, then prompts for action.
    Returns:
        - ("email", email_idx, action_idx)
        - ("event", event_idx, action_idx)
        - ("skip", -1, -1) if user wants to skip
        - ("quit", -1, -1) if user wants to quit
        - None if no actionable items or invalid input format after trying
    PM Consideration: Clear separation of concerns. This function displays and gets choice.
    The actual execution of the choice happens elsewhere.
    Provide clear instructions to the user.
    """
    actionable_items_present = False

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
            # PM: Only show events that have LLM-suggested actions to keep it focused.
            if event_data.get('suggested_actions'):
                display_calendar_event_summary(len(important_emails_llm) + i + 1, event_data)
                actionable_items_present = True
    else:
        print(f"{Fore.GREEN}No upcoming events with specific suggestions.{Style.RESET_ALL}")

    if not actionable_items_present:
        print(f"\n{Fore.GREEN}All caught up for now!{Style.RESET_ALL}")
        return None # No actions to take

    print(f"\n{Style.BRIGHT}Enter your choice (e.g., '1a' for action 'a' on item '1', 's' to skip, 'q' to quit cycle):{Style.RESET_ALL}")

    # PM: Simple input validation. Could be more robust with regex.
    user_choice_str = input(f"{Fore.CYAN}> {Style.RESET_ALL}").strip().lower()

    if user_choice_str == 's' or user_choice_str == 'skip':
        return "skip", -1, -1
    if user_choice_str == 'q' or user_choice_str == 'quit':
        return "quit", -1, -1

    if len(user_choice_str) >= 2 and user_choice_str[:-1].isdigit() and user_choice_str[-1].isalpha():
        item_num_chosen = int(user_choice_str[:-1])
        action_char_chosen = user_choice_str[-1]
        action_idx_chosen = ord(action_char_chosen) - ord('a')

        # Determine if it's an email or event
        if 1 <= item_num_chosen <= len(important_emails_llm):
            item_type = "email"
            actual_item_idx = item_num_chosen - 1
            # Check if action_idx_chosen is valid for this email
            if 0 <= action_idx_chosen < len(important_emails_llm[actual_item_idx].get('suggested_actions', [])):
                return item_type, actual_item_idx, action_idx_chosen
            else:
                print(f"{Fore.RED}Invalid action '{action_char_chosen}' for email {item_num_chosen}.{Style.RESET_ALL}")

        elif len(important_emails_llm) < item_num_chosen <= (len(important_emails_llm) + len(processed_events_llm)):
            item_type = "event"
            actual_item_idx = item_num_chosen - 1 - len(important_emails_llm)
             # Check if action_idx_chosen is valid for this event
            if 0 <= action_idx_chosen < len(processed_events_llm[actual_item_idx].get('suggested_actions', [])):
                return item_type, actual_item_idx, action_idx_chosen
            else:
                print(f"{Fore.RED}Invalid action '{action_char_chosen}' for event {item_num_chosen}.{Style.RESET_ALL}")
        else:
            print(f"{Fore.RED}Invalid item number '{item_num_chosen}'.{Style.RESET_ALL}")
    else:
        if user_choice_str: # Only print error if they typed something invalid, not if they just hit enter
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
            return "send"
        if choice in ['e', 'edit']:
            return "edit"
        if choice in ['c', 'cancel', '']: # Empty input defaults to cancel
            return "cancel"
        print(f"{Fore.RED}Invalid choice. Please enter S, E, or C.{Style.RESET_ALL}")

def get_send_edit_save_cancel_confirmation(draft_text: str, service_name: str = "email") -> str:
    """
    Displays a draft and asks for confirmation: Send, Edit, Save as Draft, or Cancel.
    Returns "send", "edit", "save_draft", or "cancel".
    """
    print_header(f"Draft {service_name.capitalize()}")
    print(f"{Fore.WHITE}{draft_text}{Style.RESET_ALL}")

    while True:
        choice = input(
            f"{Fore.CYAN}Action: ({Style.BRIGHT}{Fore.GREEN}S{Style.RESET_ALL}{Fore.CYAN})end Reply, "
            f"({Style.BRIGHT}{Fore.YELLOW}E{Style.RESET_ALL}{Fore.CYAN})dit, "
            f"Save as (D)raft, " # New option
            f"({Style.BRIGHT}{Fore.RED}C{Style.RESET_ALL}{Fore.CYAN})ancel? {Style.RESET_ALL}"
        ).strip().lower()
        if choice in ['s', 'send']:
            return "send_reply" # Be specific
        if choice in ['e', 'edit']:
            return "edit"
        if choice in ['d', 'draft', 'save draft']: # New
            return "save_draft"
        if choice in ['c', 'cancel', '']:
            return "cancel"
        print(f"{Fore.RED}Invalid choice. Please enter S, E, D, or C.{Style.RESET_ALL}")


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
    confirmation = confirmation = get_send_edit_save_cancel_confirmation(draft)
    print(f"User confirmation for draft: {confirmation}")

    print("\n--- Test complete ---")
