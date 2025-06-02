# config_manager.py
import os
import json
from dotenv import load_dotenv # Make sure this is imported
from pathlib import Path
from datetime import datetime, timezone

# --- Constants for User Config Keys ---
USER_EMAIL_KEY = "USER_EMAIL_FOR_COMPOSIO"
USER_PERSONA_KEY = "USER_PERSONA_DESCRIPTION"
USER_PRIORITIES_KEY = "USER_PRIORITIES"
NOTIFICATION_PREFS_KEY = "NOTIFICATION_PREFERENCES"
GMAIL_MCP_URL_KEY = "GMAIL_MCP_URL_USER"
CALENDAR_MCP_URL_KEY = "CALENDAR_MCP_URL_USER"
LAST_EMAIL_CHECK_KEY = "LAST_EMAIL_CHECK_TIMESTAMP"
SCHED_FREQUENCY_MINUTES_KEY = "SCHED_FREQUENCY_MINUTES"
SCHED_ACTIVE_DAYS_KEY = "SCHED_ACTIVE_DAYS" # e.g., [0, 1, 2, 3, 4] for Mon-Fri
SCHED_ACTIVE_START_HOUR_KEY = "SCHED_ACTIVE_START_HOUR" # 0-23
SCHED_ACTIVE_END_HOUR_KEY = "SCHED_ACTIVE_END_HOUR"   # 0-23 (e.g., 18 for up to 6 PM)
WORK_START_HOUR_KEY = "WORK_START_HOUR" # For free slot calculation
WORK_END_HOUR_KEY = "WORK_END_HOUR"     # For free slot calculation


# --- Constants for .env Keys (Developer/System Config) ---
ENV_GOOGLE_API_KEY = "GOOGLE_API_KEY" # Renamed from GEMINI_API_KEY
ENV_GMAIL_MCP_SERVER_UUID = "GMAIL_MCP_SERVER_UUID"
ENV_CALENDAR_MCP_SERVER_UUID = "CALENDAR_MCP_SERVER_UUID"

CONFIG_DIR_NAME = ".proactive_assistant"
USER_CONFIG_FILE_NAME = "user_config.json"
ENV_FILE_NAME = ".env"

HOME_DIR = Path.home()
CONFIG_DIR_PATH = HOME_DIR / CONFIG_DIR_NAME
USER_CONFIG_FILE_PATH = CONFIG_DIR_PATH / USER_CONFIG_FILE_NAME

def load_env_vars():
    project_root = Path(__file__).resolve().parent
    env_path = project_root / ENV_FILE_NAME
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True) # ensure override is true
        # print(f"Loaded environment variables from: {env_path}")
    else:
        # print(f".env file not found at {env_path}.")
        pass

    config = {
        ENV_GOOGLE_API_KEY: os.getenv(ENV_GOOGLE_API_KEY), # Use new constant
        ENV_GMAIL_MCP_SERVER_UUID: os.getenv(ENV_GMAIL_MCP_SERVER_UUID),
        ENV_CALENDAR_MCP_SERVER_UUID: os.getenv(ENV_CALENDAR_MCP_SERVER_UUID),
    }
    # For debugging if GOOGLE_API_KEY is correctly loaded into environment:
    # print(f"os.environ['GOOGLE_API_KEY'] after load_dotenv: {os.getenv('GOOGLE_API_KEY')}")
    return config

DEV_CONFIG = load_env_vars() # This runs on module import

# --- User Configuration Management ---
def _ensure_config_dir_exists():
    """Ensures the user-specific configuration directory exists."""
    CONFIG_DIR_PATH.mkdir(parents=True, exist_ok=True)

def load_user_config():
    """Loads user-specific configuration from user_config.json.
    Returns an empty dict if the file doesn't exist or is invalid.
    """
    _ensure_config_dir_exists()
    if USER_CONFIG_FILE_PATH.exists():
        try:
            with open(USER_CONFIG_FILE_PATH, 'r') as f:
                # print(f"Loading user config from: {USER_CONFIG_FILE_PATH}")
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Error: Could not decode {USER_CONFIG_FILE_NAME}. Starting with fresh config.")
            return {}
        except Exception as e:
            print(f"Error loading {USER_CONFIG_FILE_NAME}: {e}. Starting with fresh config.")
            return {}
    return {}

def save_user_config(config_data):
    """Saves user-specific configuration to user_config.json."""
    _ensure_config_dir_exists()
    try:
        with open(USER_CONFIG_FILE_PATH, 'w') as f:
            json.dump(config_data, f, indent=2)
        # print(f"User config saved to: {USER_CONFIG_FILE_PATH}")
        return True
    except Exception as e:
        print(f"Error saving user config to {USER_CONFIG_FILE_PATH}: {e}")
        return False

def get_user_config_value(key, default=None):
    """Gets a specific value from the user config, returning a default if not found."""
    config = load_user_config()
    return config.get(key, default)

def update_user_config_value(key, value):
    """Updates a specific value in the user config and saves it."""
    config = load_user_config()
    config[key] = value
    return save_user_config(config)

def get_last_email_check_timestamp():
    """Gets the last email check timestamp. Returns None if not set."""
    ts_str = get_user_config_value("LAST_EMAIL_CHECK_TIMESTAMP")
    if ts_str:
        try:
            return datetime.fromisoformat(ts_str)
        except ValueError:
            print(f"Warning: Invalid timestamp format for LAST_EMAIL_CHECK_TIMESTAMP: {ts_str}")
            return None
    return None

def set_last_email_check_timestamp(timestamp: datetime = None):
    """Sets the last email check timestamp to the given timestamp or now if None."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    update_user_config_value("LAST_EMAIL_CHECK_TIMESTAMP", timestamp.isoformat())

# --- Initial Setup ---
# Load environment variables when the module is imported
DEV_CONFIG = load_env_vars()

# --- Main for testing this module ---
if __name__ == "__main__":
    print("--- Testing config_manager.py ---")

    print("\n1. Developer Environment Variables (from .env or system):")
    print(f"  GEMINI_API_KEY: {'********' if DEV_CONFIG.get('GEMINI_API_KEY') else 'Not Set'}")

    print(f"\nUser config will be stored at: {USER_CONFIG_FILE_PATH}")

    print("\n2. Loading initial user config:")
    initial_user_cfg = load_user_config()
    print(f"  Initial user_config.json content: {initial_user_cfg}")

    print("\n3. Updating and saving user config values:")
    update_user_config_value("USER_EMAIL_FOR_COMPOSIO", "test_user@example.com")
    update_user_config_value("USER_PERSONA", "A busy bee")
    update_user_config_value("NOTIFICATION_PREFS", {"email": "important", "calendar": "on"})

    # Test setting and getting timestamp
    print("\n   Testing timestamp functions:")
    set_last_email_check_timestamp() # Set to now
    loaded_ts = get_last_email_check_timestamp()
    print(f"   Loaded timestamp: {loaded_ts} (Type: {type(loaded_ts)})")

    # Test specific value retrieval
    email_pref = get_user_config_value("NOTIFICATION_PREFS", {}).get("email")
    print(f"   Retrieved email notification preference: {email_pref}")


    print("\n4. Reloading user config to verify save:")
    reloaded_user_cfg = load_user_config()
    print(f"  Reloaded user_config.json content: {json.dumps(reloaded_user_cfg, indent=2)}")

    print("\n5. Cleaning up test user config (optional - inspect the file first if you want):")
    # To clean up for next test, you can delete the file or set it to empty
    if USER_CONFIG_FILE_PATH.exists():
        # USER_CONFIG_FILE_PATH.unlink()
        # print(f"  Deleted {USER_CONFIG_FILE_PATH} for cleanup.")
        # OR save empty
        save_user_config({})
        print(f"  Reset {USER_CONFIG_FILE_PATH} to empty for cleanup.")

    print("\n--- Test complete ---")
