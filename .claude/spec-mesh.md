# Del-Fi — Mesh Adapter Specification

<!-- Parent: .claude/claude.md §6 -->
<!-- Related: spec-config.md §mesh keys -->

---

## 1. MeshAdapter ABC

All radio adapters implement `MeshAdapter` from `del_fi/mesh/base.py`.

```python
import abc

class MeshAdapter(abc.ABC):

    @abc.abstractmethod
    def connect(self) -> None:
        """
        Establish connection to the radio hardware.
        Raises: MeshConnectionError on failure.
        Should be idempotent — calling connect() on an already-connected adapter
        is a no-op (not an error).
        """

    @abc.abstractmethod
    def send_dm(self, dest: str, text: str) -> None:
        """
        Send a direct message to a node.
        dest: hardware node ID string (hex, e.g. "!a1b2c3d4")
        text: UTF-8 string, already formatted and enforced ≤ 230 bytes by Formatter.
        Raises: MeshSendError on failure (adapter logs and discards — does not crash daemon).
        """

    @abc.abstractmethod
    def close(self) -> None:
        """
        Disconnect cleanly. Called on daemon shutdown (SIGINT/SIGTERM).
        Must not raise.
        """

    def reconnect_loop(self) -> None:
        """
        Optional. Override to provide background reconnection.
        Default implementation does nothing.
        Called from a daemon background thread — must loop indefinitely until
        daemon shutdown is signalled.
        """
        pass

    def on_message(self, callback) -> None:
        """
        Register the dispatcher callback.
        callback signature: (sender_id: str, text: str) -> None
        Must be called before connect(). Adapters call the callback from the
        listener thread; dispatcher must be thread-safe (uses queue.Queue internally).
        """
        self._callback = callback
```

### Exception types

```python
class MeshConnectionError(Exception): ...
class MeshSendError(Exception): ...
```

Both are defined in `del_fi/mesh/base.py`.

### Invariants

- The dispatcher callback is always registered before `connect()` is called.
- `send_dm()` receives text that has already passed through `Formatter`.
  Adapters must not re-encode, re-format, or truncate the text.
- Adapters must not call `send_dm()` recursively from the listener callback.
- Adapter threads must not import from `del_fi/core/` — dependency direction is
  `core/ → mesh/`, not the reverse.

---

## 2. Meshtastic Adapter

File: `del_fi/mesh/meshtastic_adapter.py`

### 2.1 Connection modes

| Mode | Config `mesh_type` | When to use |
|------|-------------------|-------------|
| Serial | `meshtastic-serial` | Direct USB/UART connection |
| TCP | `meshtastic-tcp` | Wi-Fi or networked radio (same LAN) |
| BLE | `meshtastic-ble` | Bluetooth (slower; use only on hardware with BLE) |

Config examples:

```yaml
mesh_type: meshtastic-serial
serial_port: /dev/ttyUSB0       # or null for auto-detect

mesh_type: meshtastic-tcp
tcp_host: 192.168.1.42
tcp_port: 4403                  # default Meshtastic TCP port

mesh_type: meshtastic-ble
ble_address: "AA:BB:CC:DD:EE:FF"  # null for auto-scan
```

### 2.2 Message subscription

The adapter subscribes to Meshtastic's pub/sub interface:

```python
from meshtastic import portnums_pb2
from pubsub import pub

pub.subscribe(self._on_receive, "meshtastic.receive.text")
```

Handler:

```python
def _on_receive(self, packet, interface):
    msg_id = packet.get("id")
    if msg_id in self._seen_ids:
        return                          # dedup
    self._seen_ids.add(msg_id)
    if len(self._seen_ids) > 1000:     # prune
        self._seen_ids = set(list(self._seen_ids)[-500:])

    decoded = packet.get("decoded", {})
    text = decoded.get("text", "").strip()
    sender = packet.get("fromId")
    
    if not text or not sender:
        return
    
    # broadcast: log only, do not reply
    to = packet.get("toId")
    if to == "^all":
        log.debug("broadcast from %s (ignored): %s", sender, text[:50])
        return
    
    self._callback(sender, text)
```

### 2.3 Rate limiting

Freeform queries (not commands) are rate-limited per sender.
Commands (`!`-prefix) bypass the rate limiter always.

```python
# per-sender timestamp tracking
def _is_rate_limited(self, sender: str) -> bool:
    now = time.monotonic()
    last = self._rate_timestamps.get(sender, 0.0)
    if now - last < self._rate_limit_seconds:
        return True
    self._rate_timestamps[sender] = now
    return False
```

Config key: `rate_limit_seconds` (default: 30). 0 disables rate limiting.

Rate limit response: `"[rate limited — wait {remaining:.0f}s]"`

### 2.4 Message deduplication

Meshtastic may deliver the same packet multiple times (mesh flooding). The adapter
tracks seen message IDs in a `set`. The set is pruned when it exceeds 1000 entries,
keeping the most recent 500 (sliding window by insertion order).

The dedup set is in-memory only; a daemon restart clears it. Duplicate handling
across restart is not required.

### 2.5 Outbound inter-chunk delay

When sending multi-chunk responses, insert a delay between chunks to avoid
flooding the mesh:

```python
INTER_CHUNK_DELAY_SECONDS = 3.0
```

Config key: `chunk_delay_seconds` (default: 3.0). Minimum enforced: 1.0 second.

### 2.6 Reconnect loop

The `reconnect_loop()` implementation for Meshtastic:

```python
def reconnect_loop(self) -> None:
    while not self._shutdown.is_set():
        if not self._connected:
            try:
                self.connect()
            except MeshConnectionError as e:
                log.warning("Reconnect failed: %s. Retrying in 30s.", e)
                self._shutdown.wait(30)
        else:
            self._shutdown.wait(5)   # poll health every 5s
```

A `threading.Event` (`self._shutdown`) is set on `close()` to exit the loop.

---

## 3. MeshCore Adapter

File: `del_fi/mesh/meshcore_adapter.py`

**Status: Stub.** The MeshCore Python library is not yet stable enough for
production integration. The adapter scaffolding is maintained so that a future
implementer can drop in the library calls without restructuring the codebase.

```python
class MeshCoreAdapter(MeshAdapter):
    
    def connect(self) -> None:
        raise NotImplementedError(
            "MeshCore adapter is a stub. "
            "Set mesh_type: meshtastic-serial in config to use a real radio."
        )
    
    def send_dm(self, dest: str, text: str) -> None:
        raise NotImplementedError("MeshCore adapter is a stub.")
    
    def close(self) -> None:
        pass  # no-op; never connected
```

When the MeshCore library stabilises, implement following the same pattern as
`MeshtasticAdapter`: pub/sub callback, dedup, rate limiting, reconnect loop.

---

## 4. Simulator Adapter

File: `del_fi/mesh/simulator.py`

Used for development and testing. Reads messages from stdin, writes to stdout.
No radio hardware required.

### 4.1 Message format

Input lines (stdin):

```
!a1b2c3d4> message text here
```

The prefix `!a1b2c3d4>` specifies the sender node ID. If omitted, the default
sender ID `!simulator` is used.

Output (stdout):

```
[RIDGELINE → !a1b2c3d4] Response text here
```

### 4.2 Colorized output

The simulator uses ANSI colors to distinguish participants. Colors are suppressed
if stdout is not a TTY (`sys.stdout.isatty()` is False).

| Role | Color | ANSI |
|------|-------|------|
| Outgoing (oracle) | Cyan | `\033[36m` |
| Incoming (user) | Yellow | `\033[33m` |
| System / info | Dim | `\033[2m` |

```
\033[2m[sim] Enter messages as: !nodeID> text  (or just: text)\033[0m
\033[33m!a1b2c3d4> what birds are common here?\033[0m
\033[36m[RIDGELINE → !a1b2c3d4] Common species: Clark's Nutcracker, Stellar's Jay,\033[0m
\033[36mAmerican Dipper. Peak activity May–Sept at elevation.\033[0m
```

### 4.3 Implementation notes

- The simulator reads from `sys.stdin` in the main thread and calls `self._callback()`.
- `send_dm()` writes to `sys.stdout`.
- The inter-chunk delay is suppressed in simulator mode (config override `chunk_delay_seconds: 0`).
- `KeyboardInterrupt` in simulator mode triggers clean shutdown via the normal
  SIGINT handler — the simulator does not catch it independently.

### 4.4 Usage

```bash
python main.py --simulator [--config PATH]

# Pipe mode (scripted testing)
echo "!test1> what is the current temperature?" | python main.py --simulator
```

---

## 5. Adapter Registration

The active adapter is resolved from the `mesh_type` config key at startup:

```python
# del_fi/config.py
MESH_ADAPTERS = {
    "meshtastic-serial": "del_fi.mesh.meshtastic_adapter.MeshtasticAdapter",
    "meshtastic-tcp":    "del_fi.mesh.meshtastic_adapter.MeshtasticAdapter",
    "meshtastic-ble":    "del_fi.mesh.meshtastic_adapter.MeshtasticAdapter",
    "meshcore":          "del_fi.mesh.meshcore_adapter.MeshCoreAdapter",
    "simulator":         "del_fi.mesh.simulator.SimulatorAdapter",
}
```

When `--simulator` CLI flag is given, the adapter is forced to `simulator`
regardless of `mesh_type` config.

---

## 6. Adding a New Adapter

1. Create `del_fi/mesh/<name>_adapter.py`.
2. Subclass `MeshAdapter`, implement `connect()`, `send_dm()`, `close()`.
3. Optionally implement `reconnect_loop()`.
4. Register in `config.py → MESH_ADAPTERS`.
5. Add tests in `tests/test_mesh.py` covering:
   - Successful `connect()` and `close()`.
   - `send_dm()` delivers text to the correct destination.
   - Rate limiting: a freeform query is blocked after threshold; command is not.
   - Deduplication: duplicate message ID is not forwarded to callback.

---

<!-- End of spec-mesh.md -->
