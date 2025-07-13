import serial
from threading import Thread, Event

# ---------- SETTINGS -------------
TIMEOUT = 1

# ---------------------------------
class SerialClient:
    def __init__(self, device, baud=115200):
        self.shutdown = Event()
        self._on_package_recieved = None
        self.serial_port = serial.Serial(device, baudrate=baud, timeout=TIMEOUT)



    def set_on_package_recieved(self, callback):
        self._on_package_recieved = callback

    def _fire_package_recieved(self, package):
        if self._on_package_recieved is not None:
            self._on_package_recieved(package)

    def extract_package(self, data):
        # assume that data is one line where bytes are separated by spaces
        #
        # register       address  size  data
        # BANK           0x00     1     current bank 0-255
        # CHANNEL_1      0x01     2     bitmask => 15: channel record, 10-14: not used, 0-9: mix value 0-1023
        # ...
        # CHANNEL_5      0x05     2     bitmask => 15: channel record, 10-14: not used, 0-9: mix value 0-1023
        # SAMPLER_STATUS 0x10     2     bitmask => 15: armed status, 10-14: not used, 0-9: threshold 0-1023

        parts = data.split()
        if(len(parts) < 1 or len(parts) > 3):
            return {'valid': False, 'data': data}
        
        address = int(parts[0], 16)

        if len(parts) == 2 and address == 0x00:
            return {'valid': True, 'bank': int(parts[1], 16)}
        elif len(parts) == 3 and address >= 0x01 and address <= 0x05:
            two_parts = parts[1] + parts[2]
            return {'valid': True, 'channel': address, 'mix_value': int(two_parts, 16) and b0000001111111111, 'armed': bool(int(two_parts, 16) & 0b1000000000000000)}
        elif len(parts) == 3 and address == 0x10:
            two_parts = parts[1] + parts[2]
            return {'valid': True, 'threshold': int(two_parts, 16) & 0b0000001111111111, 'armed': bool(int(two_parts, 16) & 0b1000000000000000)}
        else:
            return {'valid': False, 'data': data}

    def read_serial_thread(self):
        while not self.shutdown.is_set():
            if self.serial_port.in_waiting > 0:
                data = self.serial_port.readline().decode('utf-8').strip()
                if data:
                    package = self.extract_package(data)
                    if package:
                        self._fire_package_recieved(package)
                   

    def begin(self):
        self.shutdown.clear()
        Thread(target=self.read_serial_thread, daemon=True).start()
        print(f"serial device {self.device} started at {self.baud} baud")

    def end(self):
        self.shutdown.set()
        if self.serial_port.is_open:
            self.serial_port.close()