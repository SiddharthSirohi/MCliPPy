# MCliPPy 🤖✉️🗓️

**MCliPPy (MCP + CLI + Python + Clippy homage)** is a proactive, terminal-based personal assistant designed to streamline your daily workflow by intelligently managing your Gmail and Google Calendar. Powered by Google's Gemini models and leveraging the Model Context Protocol (MCP) via Composio.dev for secure access to your services, MCliPPy keeps you ahead of your schedule and communications.


## ✨ Features

*   **Proactive Email Triage:**
    *   Periodically checks your Gmail for new unread emails (within the last 24 hours).
    *   Uses Gemini LLM to identify important/actionable emails based on your configured persona and priorities.
    *   Provides concise summaries of important emails.
*   **Intelligent Calendar Overview:**
    *   Fetches your upcoming Google Calendar events for the next 24 hours.
    *   Uses Gemini LLM to highlight key events and potential scheduling nuances.
*   **Actionable Quick Actions:**
    *   For important emails:
        *   **Draft & Send Replies:** Let MCliPPy (with Gemini's help) draft contextual replies. Review, edit iteratively in the terminal, and send directly.
        *   **Find Free Slots for Meetings:** If an email suggests a meeting, MCliPPy can check your calendar for free slots (within your defined working hours) and help incorporate them into your reply draft.
        *   **Create Calendar Events from Email:** Intelligently parse details from emails to suggest and create new calendar events.
        *   **Mark as Read:** Automatically marks email threads as read after you've replied.
    *   For upcoming calendar events:
        *   **Update Event Details:** Interactively modify event titles, times, durations, descriptions, locations, attendees, and add/ensure Google Meet links.
        *   **Delete Events:** Quickly cancel or remove events from your calendar.
        *   **Create Follow-up/Related Events:** Schedule new events based on existing ones.
        *   **Find Free Slots:** Check availability around existing events or for new ones.
*   **macOS System Notifications:**
    *   Get native macOS notifications for new important emails or actionable calendar events.
    *   Click "Open Assistant" on the notification to launch an interactive terminal session pre-loaded with the relevant items.
*   **Scheduled Background Operation:**
    *   Configurable to run automatically in the background on macOS using `launchd`.
    *   Define frequency, active days, and active hours for checks.
*   **Personalized for You:**
    *   Initial setup flow to define your work persona, priorities, notification preferences, and working hours, which guides the LLM's intelligence.
*   **Terminal-Based UI:**
    *   Clean, color-coded interface using `colorama`.
    *   Interactive menus for taking actions.

## 🚀 Getting Started

These instructions are for macOS.

### Prerequisites

1.  **Python 3.10+:** Ensure you have Python installed.
2.  **Virtual Environment Tool (`uv` recommended):**
    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh 
    # or pip install uv
    ```
3.  **`terminal-notifier` (for macOS notifications):**
    ```bash
    brew install terminal-notifier
    ```
4.  **Composio Account & API Key:**
    *   Sign up at [Composio.dev](https://www.composio.dev/).
    *   Obtain your API Key from your Composio dashboard. Composio offers a Hobby tier for generous personal use.
5.  **Google Gemini API Key:**
    *   Obtain an API key from [Google AI Studio](https://aistudio.google.com/app/apikey). Google offers generous rate limits for a wide class of models. Gemini Flash 2.5 Preview recommended.
6.  **Composio MCP Server Setup:**
    *   You need to create two MCP Server instances via the Composio API: one for Gmail and one for Google Calendar.
    *   **Gmail MCP Server:**
        1.  In your Composio Dashboard: Go to Apps > Integrations > Add Integration for "Gmail".
        2.  Configure OAuth2, select necessary scopes (e.g., `gmail.readonly`, `gmail.modify`, `gmail.compose`). *Ensure "Use your own Developer App" is OFF.*
        3.  Note the generated `integration_id` (this is your `auth_config_id`).
        4.  Using `curl` (or a tool like Postman) and your Composio API key, make a `POST` request to `https://backend.composio.dev/api/v3/mcp/servers`:
            ```bash
            curl -X POST https://backend.composio.dev/api/v3/mcp/servers \
            -H "x-api-key: YOUR_COMPOSIO_API_KEY" \
            -H "Content-Type: application/json" \
            -d '{
              "name": "MCliPPyGmailServer",
              "auth_config_id": "YOUR_GMAIL_INTEGRATION_ID",
              "apps": ["gmail"],
              "allowed_tools": [
                "GMAIL_FETCH_EMAILS",
                "GMAIL_REPLY_TO_THREAD",
                "GMAIL_MODIFY_THREAD_LABELS" 
                # Add other Gmail tool slugs as needed, e.g., GMAIL_CREATE_EMAIL_DRAFT
              ]
            }'
            ```
        5.  From the response, note the **server UUID** for Gmail.
    *   **Google Calendar MCP Server:**
        1.  In Composio Dashboard: Add Integration for "Google Calendar".
        2.  Scopes (e.g., `calendar.events`, `calendar.readonly`, `calendar.freebusy`).
        3.  Note the `integration_id`.
        4.  `POST` to `https://backend.composio.dev/api/v3/mcp/servers`:
            ```bash
            curl -X POST https://backend.composio.dev/api/v3/mcp/servers \
            -H "x-api-key: YOUR_COMPOSIO_API_KEY" \
            -H "Content-Type: application/json" \
            -d '{
              "name": "MCliPPyCalendarServer",
              "auth_config_id": "YOUR_GCAL_INTEGRATION_ID",
              "apps": ["googlecalendar"],
              "allowed_tools": [
                "GOOGLECALENDAR_FIND_EVENT",
                "GOOGLECALENDAR_DELETE_EVENT",
                "GOOGLECALENDAR_UPDATE_EVENT",
                "GOOGLECALENDAR_CREATE_EVENT",
                "GOOGLECALENDAR_FIND_FREE_SLOTS"
                # Add other GCal tool slugs as needed
              ]
            }'
            ```
        5.  From the response, note the **server UUID** for Google Calendar.

### Installation & Setup

1.  **Clone the repository (or download the files):**
    ```bash
    git clone <link_to_mclippy_repo>
    cd mclippy 
    ```
2.  **Create and activate a virtual environment:**
    ```bash
    uv venv
    source .venv/bin/activate 
    ```
3.  **Install dependencies:**
    ```bash
    uv pip install -r requirements.txt 
    ```
4.  **Configure API Keys and Server UUIDs:**
    *   Create a `.env` file in the project root (`mclippy/`) by copying `.env.example`:
        ```bash
        cp .env.example .env
        ```
    *   Edit `.env` and fill in your values:
        ```env
        GOOGLE_API_KEY=your_google_gemini_api_key_here
        GMAIL_MCP_SERVER_UUID=your_composio_gmail_server_uuid_here
        CALENDAR_MCP_SERVER_UUID=your_composio_gcal_server_uuid_here
        ```
5.  **First Run & Signup:**
    *   Run the assistant manually for the first time to go through the setup:
        ```bash
        python assistant.py
        ```
    *   Follow the prompts to:
        *   Enter your primary email address (this will be used as your `user_id` for Composio service connections).
        *   Describe your role and work priorities (for the LLM).
        *   Set notification preferences.
        *   Set scheduling preferences (frequency, active days/hours) for background checks.
        *   Set your typical working hours (for free-slot calculations).
    *   At the end of this setup, MCliPPy will generate a `launchd .plist` file and print instructions to enable scheduled runs.
        Look for lines similar to this in the terminal output:
        ```
        A launchd agent file has been created at:
          /Users/yourusername/Library/LaunchAgents/com.yourusername.proactiveassistant.plist

        To enable automatic background checks, open Terminal and run:
          launchctl load /Users/yourusername/Library/LaunchAgents/com.yourusername.proactiveassistant.plist
        ```
    *   Copy and run the `launchctl load ...` command provided by the script in a new Terminal window or tab. This registers MCliPPy with `launchd` to run on your configured schedule.    
    *   **Important:** The first time MCliPPy tries to access Gmail or Google Calendar for your configured email, it will print an authentication URL. You need to open this URL in your browser, sign in with Google, and grant Composio permissions. After doing so, re-run `python assistant.py`.

### Running MCliPPy

*   **Background Proactive Checks (after setup & `launchctl load`):**
    *   `launchd` will run `assistant.py` at your configured intervals during active hours.
    *   Notifications will appear on your macOS desktop for important items.
    *   Logs are stored in `~/.proactive_assistant/assistant_out.log` and `assistant_err.log`.
*   **Interactive Mode:**
    *   **From Notification:** Click the "Open Assistant" button on a notification. A new terminal window will open, loading the context from that notification's check.
    *   **Manual Run:** Open Terminal, navigate to the `mclippy` project directory, activate the venv (`source .venv/bin/activate`), and run:
        ```bash
        python assistant.py
        ```
        This will perform a fresh check and then enter the interactive action loop.

## 🛠️ Code Structure

*   **`assistant.py`**: Main application orchestrator, handles command-line arguments, signup, scheduling (via `launchd` setup), calls to proactive checks, and dispatches to action handlers.
*   **`config_manager.py`**: Manages loading and saving of `.env` (developer secrets) and `user_config.json` (user-specific runtime settings, persona, preferences, temporary actionable data).
*   **`mcp_handler.py`**: Contains the `McpSessionManager` class for all interactions with Composio MCP servers (Gmail, Google Calendar). Handles connections, authentication flows via Composio helper tools, and specific tool calls (e.g., fetching emails, updating events).
*   **`llm_processor.py`**: Houses all logic for interacting with the Gemini LLM. Includes functions for summarizing emails/events, suggesting actions, drafting email replies, and parsing structured data from natural language for event creation/updates.
*   **`user_interface.py`**: Manages all terminal input and output. Provides styled prompts, displays formatted information, and handles interactive menus for user actions. Uses `colorama`.
*   **`notifier.py`**: Responsible for sending macOS system notifications using the `terminal-notifier` CLI tool. Constructs AppleScript commands to make notification buttons interactive.
*   **`calendar_utils.py`**: Contains utility functions for calendar-related logic, notably `calculate_free_slots` which processes busy times from Google Calendar to find available meeting slots.
*   **`ascii.txt`**: Stores the glorious MCliPPy welcome art!

## ⚙️ Key Mechanisms & Logic

*   **Proactive Cycle:** Fetches data from Gmail/Calendar, processes with LLM, sends macOS notification, and (if run interactively) displays actionable items.
*   **Composio MCP Integration:** Uses Composio as an intermediary to securely access Google services via standardized MCP tools, abstracting away direct OAuth and API complexities.
*   **LLM Intelligence:** Gemini is used to:
    *   Identify important emails/events.
    *   Generate concise summaries.
    *   Suggest contextually relevant quick actions.
    *   Draft email replies (incorporating found free slots if requested).
    *   Parse details from user suggestions for creating new calendar events.
*   **Interactive Quick Actions:** Users can select LLM-suggested actions or interact through menus to manage emails (reply) and calendar events (create, update, delete, find free slots).
*   **Timeout-Resilient Actions:** MCP sessions for executing user-chosen actions are established just-in-time to prevent connection timeouts during user input delays.
*   **`launchd` Scheduling:** Enables true background operation on macOS, with generated `.plist` files for user-configured schedules.
*   **Notification-Triggered Interaction:** Clicking a macOS notification opens a new terminal session with the relevant context loaded.

## 🔮 Future Enhancements (Roadmap Ideas)

*   **Natural Language Command Input:** Allow users to type free-form commands like "Reschedule my 3pm meeting to 4pm" or "Draft an email to Jane about Project X."
*   **Additional Service Integrations:** Expand to Slack, Notion, To-Do lists, etc., via more Composio MCP servers or custom-built ones.
*   **Create Generalised MCP Handlers:** Allow users to expose more tools through Composio MCP servers for use with Gemini without manual code changes.
*   **Configuration Update UI:** Allow users to change preferences (schedule, persona) after initial setup without manually editing JSON.
*   **Cross-Platform Scheduling:** Investigate background scheduling options for Windows (Task Scheduler) and Linux (cron).

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Please open an issue or submit a pull request.

<hr>

<p align="center">
<img src="https://static.wikia.nocookie.net/baldinite/images/9/92/Of9cm1.gif">
</p>

