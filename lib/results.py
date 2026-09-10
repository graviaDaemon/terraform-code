"""Status handling for command results.

Every world-changing command returns an object with `.status` (stable and 
machine-readable) and `.messaage` (localized for humans). Branch on
`.status` only - `.message` wording can change or be translated.

"ok" is not the universal spelling of success: some commands report
"started", "queued", "charging", or "complete" instead. These helpers
cover the statusses this base actually uses. Check a command's DOCS
outcome table before trusting a helper on a command that is new to you.
"""

# A transfer moved everything, or some of it.
TRANSFER_MOVED = ["ok", "partial"]

# As above, plus "no-op" - there was nothing to move. Not a failure
TRANSFER_FINE = ["ok", "partial", "no_op"]

# The machine is mid-action. Sleep and ask again rather than treating
# these as failures
TRANSIENT = ["busy", "moving", "collecting", "source_busy", "source_empty"]

def is_ok(result) -> bool:
    """True when the command reported exactly "ok"."""
    return result.status == "ok"


def is_transient(result) -> bool:
    """True when the status means "ask again shortly", not "this failed"."""
    return result.status in TRANSIENT


def moved(result) -> bool:
    """True when a transfer moved at least one unit ("ok" or "partial")."""
    return result.status in TRANSFER_MOVED


def transfer_fine(result) -> bool:
    """True when a transfer succeeded or correctly had nothing to do."""
    return result.status in TRANSFER_FINE


def complain(result, context) -> bool:
    """Print an unexpected result to the console. Always returns False
    
    For statusses the caller has no branch for:
        if not transfer_fine(sent):
            return complain(sent, "output.send")
    """
    print(f"{context}: {result.message}")
    return False


def alert(result, context) -> bool:
    """Raise an on-screen toast for an unexpected result. Returns False
    
    notify() is notify(text, level) - the second argument is the level,
    NOT more text. Build one string first; extra positional values are
    silently read as level and duration.
    """
    notify(f"{context}: {result.message}", "warn")
    return False
