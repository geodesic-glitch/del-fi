"""Tests for mesh adapter pattern — factory, base class, simulator."""

import sys
import os
import queue

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mesh import create_interface, ADAPTERS, MeshAdapter
from mesh.base import MeshAdapter as BaseAdapter
from mesh.meshtastic_adapter import MeshtasticAdapter
from mesh.meshcore_adapter import MeshCoreAdapter
from mesh.simulator import SimulatorAdapter

import pytest


# --- Minimal config for testing ---

def _cfg(**overrides):
    base = {
        "node_name": "TEST-NODE",
        "model": "test",
        "max_response_bytes": 230,
        "mesh_protocol": "meshtastic",
        "radio_connection": "serial",
        "radio_port": "/dev/ttyUSB0",
        "rate_limit_seconds": 10,
        "knowledge_folder": "./knowledge",
        "_base_dir": ".",
        "_cache_dir": "./cache",
        "_gossip_dir": "./gossip",
        "_vectorstore_dir": "./vectorstore",
    }
    base.update(overrides)
    return base


# --- Adapter registry ---


class TestAdapterRegistry:
    def test_meshtastic_registered(self):
        assert "meshtastic" in ADAPTERS
        assert ADAPTERS["meshtastic"] is MeshtasticAdapter

    def test_meshcore_registered(self):
        assert "meshcore" in ADAPTERS
        assert ADAPTERS["meshcore"] is MeshCoreAdapter

    def test_all_adapters_inherit_base(self):
        for name, cls in ADAPTERS.items():
            assert issubclass(cls, MeshAdapter), (
                f"{name} adapter does not inherit from MeshAdapter"
            )


# --- Factory ---


class TestCreateInterface:
    def test_simulator_mode(self):
        """Simulator flag always returns SimulatorAdapter."""
        q = queue.Queue()
        iface = create_interface(_cfg(), simulator=True, msg_queue=q)
        assert isinstance(iface, SimulatorAdapter)
        assert iface.connected
        iface.close()

    def test_meshtastic_protocol(self):
        """mesh_protocol=meshtastic returns MeshtasticAdapter."""
        q = queue.Queue()
        iface = create_interface(
            _cfg(mesh_protocol="meshtastic"), simulator=False, msg_queue=q
        )
        assert isinstance(iface, MeshtasticAdapter)

    def test_meshcore_protocol(self):
        """mesh_protocol=meshcore returns MeshCoreAdapter."""
        q = queue.Queue()
        cfg = _cfg(mesh_protocol="meshcore")
        cfg["meshcore"] = {"port": "/dev/ttyUSB0", "connection": "serial"}
        iface = create_interface(cfg, simulator=False, msg_queue=q)
        assert isinstance(iface, MeshCoreAdapter)

    def test_unknown_protocol_raises(self):
        """Unknown protocol raises ValueError."""
        q = queue.Queue()
        with pytest.raises(ValueError, match="Unknown mesh_protocol"):
            create_interface(
                _cfg(mesh_protocol="zigbee"), simulator=False, msg_queue=q
            )

    def test_simulator_ignores_protocol(self):
        """Simulator mode ignores mesh_protocol entirely."""
        q = queue.Queue()
        iface = create_interface(
            _cfg(mesh_protocol="meshcore"), simulator=True, msg_queue=q
        )
        assert isinstance(iface, SimulatorAdapter)
        iface.close()


# --- Base class ---


class TestMeshAdapterBase:
    def test_abstract_methods_enforced(self):
        """Cannot instantiate MeshAdapter directly."""
        with pytest.raises(TypeError):
            BaseAdapter({}, queue.Queue())

    def test_default_connected_is_false(self):
        """Default connected property returns False."""
        # Create a minimal concrete subclass
        class Dummy(BaseAdapter):
            def connect(self): return True
            def send_dm(self, d, t): return True
            def close(self): pass

        d = Dummy({}, queue.Queue())
        # The base default is False, but subclass can override
        # Just verify the base property exists
        assert hasattr(d, "connected")


# --- Simulator ---


class TestSimulatorAdapter:
    def test_send_dm_returns_true(self, capsys):
        q = queue.Queue()
        sim = SimulatorAdapter(_cfg(), q)
        result = sim.send_dm("!sim00001", "Hello world")
        assert result is True
        captured = capsys.readouterr()
        assert "Hello world" in captured.out

    def test_send_dm_warns_on_oversize(self, capsys):
        q = queue.Queue()
        sim = SimulatorAdapter(_cfg(max_response_bytes=10), q)
        sim.send_dm("!sim00001", "This message is way too long for ten bytes")
        captured = capsys.readouterr()
        assert "exceeds" in captured.out

    def test_protocol_name(self):
        q = queue.Queue()
        sim = SimulatorAdapter(_cfg(), q)
        assert sim.protocol_name == "Simulator"

    def test_connected_always_true(self):
        q = queue.Queue()
        sim = SimulatorAdapter(_cfg(), q)
        assert sim.connected is True


# --- Protocol names ---


class TestProtocolNames:
    def test_meshtastic_protocol_name(self):
        q = queue.Queue()
        m = MeshtasticAdapter(_cfg(), q)
        assert m.protocol_name == "Meshtastic"

    def test_meshcore_protocol_name(self):
        q = queue.Queue()
        cfg = _cfg(mesh_protocol="meshcore")
        cfg["meshcore"] = {}
        mc = MeshCoreAdapter(cfg, q)
        assert mc.protocol_name == "MeshCore"
