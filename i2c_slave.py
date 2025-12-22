import threading
import time
import struct
from typing import Callable, List, Optional

try:
    import pigpio
except Exception as e:
    pigpio = None


class I2CSlave:
    """
    Raspberry Pi I2C slave using pigpio BSC interface.

    Payload formats supported (from master → slave):
    - 11 bytes: [bank:u8, ch1:u16, ch2:u16, ch3:u16, ch4:u16, ch5:u16] (u16 little-endian 0–1023)
    - 6 bytes:  [bank:u8, ch1:u8,  ch2:u8,  ch3:u8,  ch4:u8,  ch5:u8 ] (u8 0–255 scaled to 0–1023)

    On valid payload, invokes callback with dict:
      { 'bank': int, 'mixes': List[int] }  where mixes are 5 values in 0–1023.
    """

    def __init__(self, address: int, poll_interval: float = 0.01):
        self.address = int(address)
        self.poll_interval = poll_interval
        self._callback: Optional[Callable[[dict], None]] = None
        self._shutdown = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pi = None
        # Current reply payload (11-byte format: bank:u8 + 5 mixes:u16 LE)
        self._tx_payload: bytes = bytes(11)

    def set_on_payload_received(self, cb: Callable[[dict], None]):
        self._callback = cb

    def _ensure_pigpio(self):
        if pigpio is None:
            raise RuntimeError("pigpio Python module not available. Install with 'pip install pigpio' and ensure pigpiod is running.")

    def begin(self):
        self._ensure_pigpio()
        self._pi = pigpio.pi()
        if not self._pi.connected:
            raise RuntimeError("Failed to connect to pigpio daemon. Start it with 'sudo systemctl start pigpiod' or 'sudo pigpiod'.")

        # Enable BSC I2C slave at given address
        self._pi.bsc_i2c(self.address)
        self._shutdown.clear()
        self._thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._thread.start()

    def end(self):
        self._shutdown.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        if self._pi:
            # Disable BSC I2C
            try:
                self._pi.bsc_i2c(0)
            finally:
                self._pi.stop()
            self._pi = None

    def _rx_loop(self):
        # Note: bsc_i2c(address) both enables and services the interface; call repeatedly to poll for data.
        while not self._shutdown.is_set():
            try:
                # Prime TX buffer so a master read gets the latest payload
                try:
                    self._pi.bsc_i2c(self.address, self._tx_payload)
                except Exception:
                    pass

                status, count, data = self._pi.bsc_i2c(self.address)
                if count and data:
                    self._handle_bytes(data)
            except Exception:
                # Avoid tight loop on error
                time.sleep(0.05)
            time.sleep(self.poll_interval)

    def _handle_bytes(self, data: bytes):
        # Accept exactly 11 or 6 bytes; ignore others.
        n = len(data)
        if n == 11:
            bank = data[0]
            mixes: List[int] = []
            for i in range(5):
                lo = data[1 + i * 2]
                hi = data[1 + i * 2 + 1]
                val = (hi << 8) | lo
                # Clamp to 0–1023
                val = max(0, min(1023, val))
                mixes.append(val)
            self._emit({'bank': bank, 'mixes': mixes})
        elif n == 6:
            bank = data[0]
            mixes = [int(v) for v in data[1:6]]
            # Scale 0–255 → 0–1023
            mixes = [max(0, min(1023, int(round(v * 1023 / 255)))) for v in mixes]
            self._emit({'bank': bank, 'mixes': mixes})
        else:
            # Ignore invalid length payloads
            return

    def _emit(self, payload: dict):
        if self._callback:
            try:
                self._callback(payload)
            except Exception:
                pass

    # -------- Reply (slave → master) helpers --------
    def set_reply_payload(self, bank: int, mixes: List[int]):
        """
        Set the reply payload the Pi will return when the I2C master performs
        a read. Uses 11-byte format: [bank:u8, ch1:u16, ..., ch5:u16] little-endian.
        """
        try:
            b = max(0, min(255, int(bank)))
            vals = [(max(0, min(1023, int(m)))) for m in (mixes or [])]
            if len(vals) < 5:
                vals = vals + [0] * (5 - len(vals))
            elif len(vals) > 5:
                vals = vals[:5]
            # Pack: bank (B) + 5 unsigned short little-endian (5H)
            self._tx_payload = struct.pack('<B5H', b, *vals)
        except Exception:
            pass
