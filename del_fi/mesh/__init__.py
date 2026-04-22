"""Mesh adapter package — protocol-agnostic radio abstraction.

The Del-Fi oracle talks to *any* mesh network through the
:class:`~del_fi.mesh.base.MeshAdapter` interface.  This module's
:func:`create_interface` factory inspects the config and returns
the right concrete adapter.

Supported protocols
-------------------
- **meshtastic** — Meshtastic LoRa radios (serial / TCP / BLE)
- **meshcore**   — MeshCore LoRa radios (stub — ready to implement)

Adding a new protocol
---------------------
1. Create ``del_fi/mesh/<protocol>_adapter.py`` with a class that
   inherits from :class:`~del_fi.mesh.base.MeshAdapter`.
2. Register it in ``ADAPTERS`` below.
3. Add any protocol-specific config defaults in ``del_fi/config.py``.
"""

import queue

from del_fi.mesh.base import MeshAdapter
from del_fi.mesh.meshtastic_adapter import MeshtasticAdapter
from del_fi.mesh.meshcore_adapter import MeshCoreAdapter
from del_fi.mesh.simulator import SimulatorAdapter

# Registry of protocol name → adapter class.
ADAPTERS: dict[str, type[MeshAdapter]] = {
    "meshtastic": MeshtasticAdapter,
    "meshcore": MeshCoreAdapter,
}


def create_interface(
    cfg: dict, simulator: bool, msg_queue: queue.Queue
) -> MeshAdapter:
    """Factory: create the appropriate mesh adapter.

    Parameters
    ----------
    cfg : dict
        Full Del-Fi config (already validated).
    simulator : bool
        If True, return a :class:`~del_fi.mesh.simulator.SimulatorAdapter`
        regardless of ``mesh_protocol``.
    msg_queue : queue.Queue
        Incoming-message queue shared with the router.
    """
    if simulator:
        return SimulatorAdapter(cfg, msg_queue)

    protocol = cfg.get("mesh_protocol", "meshtastic")
    adapter_class = ADAPTERS.get(protocol)
    if adapter_class is None:
        raise ValueError(f"Unknown mesh protocol: {protocol!r}")
    return adapter_class(cfg, msg_queue)
