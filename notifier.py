# notifier.py
import subprocess
import platform
import shlex
from pathlib import Path
from typing import Optional
import sys
import traceback

TERMINAL_NOTIFIER_PATH = "/opt/homebrew/bin/terminal-notifier" # VERIFY THIS PATH

def send_macos_notification(
    title: str,
    message: str,
    subtitle: Optional[str] = None,
    sound: bool = False,
    python_executable_for_action: Optional[str] = None,
    script_to_run_on_action: Optional[str] = None, # This is now just /path/to/assistant.py
    working_dir_for_action: Optional[str] = None
):
    # ... (platform check, base_command construction) ...
    if platform.system() != "Darwin":
        print(f"NOTIFIER: Skipping macOS notification (not on Darwin): {title} - {message}")
        return

    print(f"NOTIFIER_DEBUG: send_macos_notification called with:") # Keep this block
    print(f"  title='{title}'")
    print(f"  message='{message}'")
    print(f"  subtitle='{subtitle}'")
    print(f"  sound={sound}")
    print(f"  python_executable_for_action='{python_executable_for_action}'")
    print(f"  script_to_run_on_action='{script_to_run_on_action}'") # Will NOT have --from-notification yet
    print(f"  working_dir_for_action='{working_dir_for_action}'")

    try:
        base_command = [TERMINAL_NOTIFIER_PATH, '-title', title, '-message', message]
        if subtitle: base_command.extend(['-subtitle', subtitle])
        if sound: base_command.extend(['-sound', 'default'])
        final_command_for_subprocess = list(base_command)
        execute_command_str_for_tn = None

        if python_executable_for_action and script_to_run_on_action and working_dir_for_action:
            # Quote each path component individually for the shell command INSIDE AppleScript
            py_exec_sh = shlex.quote(str(Path(python_executable_for_action)))
            script_path_sh = shlex.quote(str(Path(script_to_run_on_action).resolve()))
            work_dir_sh = shlex.quote(str(Path(working_dir_for_action).resolve()))

            # Add the --from-notification flag here, also quoted if needed, though simple flags often don't.
            # For safety, let's quote it, or ensure it's a single token.
            action_flag = "--from-notification"

            # Command for Terminal's 'do script'
            # We want: cd /path/to/work && /path/to/venv/python /path/to/script.py --from-notification
            command_to_run_in_terminal = f"cd {work_dir_sh} && {py_exec_sh} {script_path_sh} {action_flag}"
            print(f"NOTIFIER_DEBUG: Command intended for Terminal execution by AppleScript: {command_to_run_in_terminal}")

            # AppleScript string. Double quotes inside 'do script' are fine if the whole 'do script' command is in double quotes.
            # Single quotes for osascript -e '...'
            applescript = f'''
            tell application "Terminal"
                if not (exists window 1) then reopen
                activate
                do script "{command_to_run_in_terminal.replace('"', '\\"')}"
            end tell
            '''
            # The .replace('"', '\\"') escapes any double quotes within command_to_run_in_terminal
            # for the AppleScript `do script "..."` context.

            execute_command_str_for_tn = f"osascript -e '{applescript}'"

            final_command_for_subprocess.extend(['-execute', execute_command_str_for_tn])
            final_command_for_subprocess.extend(['-actions', 'Open Assistant'])



        print(f"NOTIFIER_DEBUG: Final command array for subprocess.run: {final_command_for_subprocess}")
        if execute_command_str_for_tn:
            print(f"NOTIFIER_DEBUG: String passed to -execute: {execute_command_str_for_tn}")

        # Run terminal-notifier and capture its output/error
        process_result = subprocess.run(
            final_command_for_subprocess,
            check=False,        # Don't raise exception on non-zero exit, we'll check manually
            capture_output=True,
            text=True
        )

        print(f"NOTIFIER_DEBUG: terminal-notifier process finished.")
        print(f"  Return Code: {process_result.returncode}")
        print(f"  Stdout: {process_result.stdout.strip()}")
        print(f"  Stderr: {process_result.stderr.strip()}")

        if process_result.returncode != 0:
            print(f"NOTIFIER_ERROR: terminal-notifier execution failed.")
        # else:
            # print("NOTIFIER: Notification command sent to terminal-notifier.")

    except FileNotFoundError:
        print(f"NOTIFIER_ERROR: '{TERMINAL_NOTIFIER_PATH}' command not found. Please install it or check the path in notifier.py.")
    except Exception as e:
        print(f"NOTIFIER_ERROR: An unexpected error occurred in send_macos_notification: {e}")
        traceback.print_exc()

# Make sure it uses the explicit venv python path for testing
if __name__ == "__main__":
    import sys
    import traceback

    print("--- Testing notifier.py ---")

    project_root_for_test = Path(__file__).resolve().parent
    explicit_venv_python_for_test = project_root_for_test / ".venv" / "bin" / "python"

    test_python_exec_str : Optional[str] = None
    if explicit_venv_python_for_test.exists():
        test_python_exec_str = str(explicit_venv_python_for_test)
        print(f"NOTIFIER_TEST: Using venv python for action: {test_python_exec_str}")
    else:
        # Fallback, but print a more prominent warning for the test
        print(f"{platform.system().upper()}_NOTIFIER_TEST: CRITICAL WARNING - Venv python NOT FOUND at {explicit_venv_python_for_test}. Action button in test will likely fail to find modules.")
        # test_python_exec_str = str(Path(sys.executable).resolve()) # Fallback if really needed for some test
        # Forcing None if venv python not found, as that's the target
        test_python_exec_str = None

    test_script_path_obj = project_root_for_test / "assistant.py"
    test_work_dir_str = str(project_root_for_test)
    script_to_run_on_action_str : Optional[str] = None

    if not test_script_path_obj.exists():
        print(f"Test assistant.py not found at {test_script_path_obj}, action will not be configured.")
    elif not test_python_exec_str: # If venv python wasn't found
        print(f"Venv Python executable not determined, action will not be configured for test.")
    else:
        script_to_run_on_action_str = str(test_script_path_obj)

    send_macos_notification(
        title="Proactive Assistant Test",
        message="Click 'Open Assistant' button or notification body.",
        subtitle="Notifier.py Standalone Test",
        sound=True,
        python_executable_for_action=test_python_exec_str, # Can be None
        script_to_run_on_action=script_to_run_on_action_str, # Can be None
        working_dir_for_action=test_work_dir_str if script_to_run_on_action_str else None # Can be None
    )
    print("--- Test complete ---")
