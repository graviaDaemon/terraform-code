"""Input/Output port helpers for machines that route items.

`self` does not exist inside a library, so pass the port objects in:

    import ports
    ports.connect_pair(self.input, self.output, "inventory", "inventory", "Bio Lab")
    ports.drain(self.output)
"""

import caps

# "no_op" means there was nothing to move. That is a normal outcome.
_FINE = ["ok", "partial", "no_op"]

# Machine .input / .output ports only exist once this is researched.
FEEDERS = caps.FEEDERS


def feeders_unlocked() -> bool:
    """True when Auto Feeders research is available.

    Every machine .input / .output port depends on it, so a pipeline
    script should check once at startup rather than failing on the first
    transfer with a confusing error.
    """
    return caps.research(FEEDERS)


def require_feeders(label) -> bool:
    """Check Auto Feeders and say plainly what is missing if it is absent."""
    if feeders_unlocked():
        return True
    notify(f"{label} halted: Auto Feeders research is required before"
           f" machine input and output ports work", "warn")
    return False


def connect(port, target, label) -> bool:
    """Latch one port onto a named store. Notifies and returns False on failure.

    `target` is "inventory" only at Nocturna Base; elsewhere use a
    same-outpost Storage Bin or Warehouse name.
    """
    link = port.connect(target)
    if link.status != "ok":
        notify(f"{label} -> {target}: {link.message}", "warn")
        return False
    return True


def connect_pair(input_port, output_port, source, sink, label) -> bool:
    """Latch both ports of a machine. True only when both succeeded."""
    if not connect(input_port, source, f"{label} input port"):
        return False
    if not connect(output_port, sink, f"{label} output port"):
        return False
    return True


def drain(output_port) -> bool:
    """Push every stack in the output to its connected sink.

    Sends with "exact" property matching, which preserves glow, gene,
    Forged and Conditioned variants instead of collapsing them into the
    plain item.

    A full output blocks extract(), discard() and unload_reagents(), so
    call this before those and after anything that fills the output.

    Returns False on the first unexpected status, having printed it.
    """
    for stack in output_port.stacks():
        sent = output_port.send(stack.id, stack.count, stack.properties, "exact")
        if sent.status not in _FINE:
            print(f"output.send {stack.id}: {sent.message}")
            return False
    return True