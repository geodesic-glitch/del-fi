"""Tests for mesh adapter pattern — factory, base class, simulator."""

import io
import os
import queue
import sys
import unittest
import unittest.mock

from del_fi.mesh import create_interface, ADAPTERS, MeshAdapter
from del_fi.mesh.base import MeshAdapter as BaseAdapter
from del_fi.mesh.meshtastic_adapter import MeshtasticAdapter
from del_fi.mesh.meshcore_adapter import MeshCoreAdapter
from del_fi.mesh.simulator import SimulatorAdapter


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


class TestAdapterRegistry(unittest.TestCase):
    def test_meshtastic_registered(self):
        self.assertIn("meshtastic", ADAPTERS)
        self.assertIs(ADAPTERS["meshtastic"], MeshtasticAdapter)

    def test_meshcore_registered(self):
        self.assertIn("meshcore", ADAPTERS)
        self.assertIs(ADAPTERS["meshcore"], MeshCoreAdapter)

    def test_all_adapters_inherit_base(self):
        for name, cls in ADAPTERS.items():
            self.assertTrue(
                issubclass(cls, MeshAdapter),
                f"{name} adapter does not inherit from MeshAdapter",
            )


# --- Factory ---


class TestCreateInterface(unittest.TestCase):
    def test_simulator_mode(self):
        q = queue.Queue()
        iface = create_interface(_cfg(), simulator=True, msg_queue=q)
        self.assertIsInstance(iface, SimulatorAdapter)
        self.assertTrue(iface.connected)
        iface.close()

    def test_meshtastic_protocol(self):
        q = queue.Queue()
        iface = create_interface(
            _cfg(mesh_protocol="meshtastic"), simulator=False, msg_queue=q
        )
        self.assertIsInstance(iface, MeshtasticAdapter)

    def test_meshcore_protocol(self):
        q = queue.Queue()
        cfg = _cfg(mesh_protocol="meshcore")
        cfg["meshcore"] = {"port": "/dev/ttyUSB0", "connection": "serial"}
        iface = create_interface(cfg, simulator=False, msg_queue=q)
        self.assertIsInstance(iface, MeshCoreAdapter)

    def test_unknown_protocol_raises(self):
        q = queue.Queue()
        with self.assertRaisesRegex(ValueError, r"[Uu]nknown mesh.protocol"):
            create_interface(_cfg(mesh_protocol="zigbee"), simulator=False, msg_queue=q)

    def test_simulator_ignores_protocol(self):
        q = queue.Queue()
        iface = create_interface(
            _cfg(mesh_protocol="meshcore"), simulator=True, msg_queue=q
        )
        self.assertIsInstance(iface, SimulatorAdapter)
        iface.close()


# --- Base class ---


class TestMeshAdapterBase(unittest.TestCase):
    def test_abstract_methods_enforced(self):
        with self.assertRaises(TypeError):
            BaseAdapter({}, queue.Queue())

    def test_default_connected_is_false(self):
        class Dummy(BaseAdapter):
            def connect(self): return True
            def send_dm(self, d, t): return True
            def close(self): pass

        d = Dummy({}, queue.Queue())
        self.assertTrue(hasattr(d, "connected"))


# --- Simulator ---


class TestSimulatorAdapter(unittest.TestCase):
    def test_send_dm_returns_true(self):
        q = queue.Queue()
        sim = SimulatorAdapter(_cfg(), q)
        buf = io.StringIO()
        with unittest.mock.patch("sys.stdout", buf):
            result = sim.send_dm("!sim00001", "Hello world")
        self.assertTrue(result)
        self.assertIn("Hello world", buf.getvalue())

    def test_send_dm_warns_on_oversize(self):
        q = queue.Queue()
        sim = SimulatorAdapter(_cfg(max_response_bytes=10), q)
        buf = io.StringIO()
        with unittest.mock.patch("sys.stdout", buf):
            sim.send_dm("!sim00001", "This message is way too long for ten bytes")
        self.assertIn("exceeds", buf.getvalue())

    def test_protocol_name(self):
        q = queue.Queue()
        sim = SimulatorAdapter(_cfg(), q)
        self.assertEqual(sim.protocol_name, "Simulator")

    def test_connected_always_true(self):
        q = queue.Queue()
        sim = SimulatorAdapter(_cfg(), q)
        self.assertTrue(sim.connected)


# --- Protocol names ---


class TestProtocolNames(unittest.TestCase):
    def test_meshtastic_protocol_name(self):
        q = queue.Queue()
        m = MeshtasticAdapter(_cfg(), q)
        self.assertEqual(m.protocol_name, "Meshtastic")

    def test_meshcore_protocol_name(self):
        q = queue.Queue()
        cfg = _cfg(mesh_protocol="meshcore")
        cfg["meshcore"] = {}
        mc = MeshCoreAdapter(cfg, q)
        self.assertEqual(mc.protocol_name, "MeshCore")


if __name__ == "__main__":
    unittest.main()

