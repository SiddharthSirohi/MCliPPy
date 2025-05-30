# notifier.py
import subprocess
import platform

# PM Consideration: Make notifications concise and actionable.
# The title should immediately tell the user what the notification is about.
# The message should give a quick summary of what needs attention.
# We could also consider adding a subtitle or sound, but let's keep it simple for MVP.

def send_macos_notification(title: str, message: str, subtitle: str = None, sound: bool = False):
    """
    Sends a macOS notification using the terminal-notifier command-line tool.
    Ensures it only runs on macOS.
    """
    if platform.system() != "Darwin":
        print(f"NOTIFIER: Skipping macOS notification (not on Darwin): {title} - {message}")
        return

    try:
        command = ['terminal-notifier', '-title', title, '-message', message]
        if subtitle:
            command.extend(['-subtitle', subtitle])
        if sound: # PM: Sound can be good for urgent, but off by default to be less intrusive.
            command.extend(['-sound', 'default'])

        # PM: Add an app icon for better visual identity (if we had one)
        # command.extend(['-appIcon', 'path/to/your/app_icon.png'])

        # PM: Add an action button that could, in a more advanced version,
        # bring the assistant's terminal window to the front.
        # For now, it just dismisses the notification.
        # command.extend(['-actions', 'Show Assistant'])
        # We'd need to handle the action if we enable this.

        subprocess.run(command, check=True)
        # print(f"NOTIFIER: Sent notification: {title} - {message}")
    except FileNotFoundError:
        print("NOTIFIER_ERROR: 'terminal-notifier' command not found. Please install it (e.g., 'brew install terminal-notifier').")
    except subprocess.CalledProcessError as e:
        print(f"NOTIFIER_ERROR: Failed to send notification: {e}")
    except Exception as e:
        print(f"NOTIFIER_ERROR: An unexpected error occurred: {e}")

if __name__ == "__main__":
    print("--- Testing notifier.py ---")
    send_macos_notification(
        title="Proactive Assistant Test",
        message="This is a test notification. 1 important email, 2 events.",
        subtitle="Quick Update",
        sound=True
    )
    send_macos_notification(
        title="Another Test",
        message="Just checking..."
    )
    print("--- Test complete ---")
    print("Check your macOS Notification Center.")
