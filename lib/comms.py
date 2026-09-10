"""Earth uplink helpers.

The transmitter has to be connected to a destination before it will
send anything. Every contract submission and telemetry push in this base
is the same three calls, so they live here once.
"""

TRANSMITTER_ID = "transmitter"
EARTH = "earth"


def transmit_earth(channel, payload, transmitter_id=TRANSMITTER_ID):
    """Connect the transmitter to Earth and send `payload` on `channel`.

    `channel` is `self.contract.id` for a contract submission, or a plain
    name such as "current_temperature" for telemetry.

    Returns the transmit result - check `.status` at the call site if the
    outcome matters.
    """
    tx = get_component(transmitter_id)
    tx.connect(EARTH)
    return tx.transmit(channel, payload)