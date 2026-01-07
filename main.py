import os
import atexit
import signal
import RPi.GPIO as GPIO
import time
from threading import Thread, Event
from debounced_button import DebouncedButton
from sampler import Sampler
from serial_client import SerialClient
from audio_engine import AudioEngine

# Setup

# GPIO pin configuration
trigger_pins = [17,22,24,5,12]
led_pins = [27,23,25,6,13]
sample_pin = 26
sample_led = 16

# general vars
sample_mode = False
bank = 0
bank_is_readonly = False
recording_blink_threads = {}
recording_blink_flags = {}
recording_blink_intervals = {}
current_mixes = [0, 0, 0, 0, 0]

# Map triggers to sounds and LEDs
sound_files = {
    17: "sound0.wav",
    22: "sound1.wav",
    24: "sound2.wav",
    5: "sound3.wav",
    12: "sound4.wav"
}
led_map = {
    17: 27,
    22: 23,
    24: 25,
    5: 6,
    12: 13
}
channel_map = {
    17: 0,
    22: 1,
    24: 2,
    5: 3,
    12: 4
}

DEBOUNCE_TIME = 0.001


channel_buttons = {
    17: DebouncedButton(pin=17, debounce_time=DEBOUNCE_TIME),
    22: DebouncedButton(pin=22, debounce_time=DEBOUNCE_TIME),
    24: DebouncedButton(pin=24, debounce_time=DEBOUNCE_TIME),
    5: DebouncedButton(pin=5, debounce_time=DEBOUNCE_TIME),
    12: DebouncedButton(pin=12, debounce_time=DEBOUNCE_TIME)
}
sample_button = DebouncedButton(pin=sample_pin, debounce_time=DEBOUNCE_TIME)

sampler = Sampler()
serial_client = SerialClient(device='/dev/ttyACM0')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
engine = AudioEngine(samplerate=48000, blocksize=256)
engine.set_direct_gain_global(0.6)
engine.start()

def _shutdown():
    try:
        engine.stop()
    finally:
        GPIO.cleanup()

atexit.register(_shutdown)

def _signal_shutdown(signum, frame):
    _shutdown()
    # Exit immediately to avoid double cleanup
    os._exit(0)

signal.signal(signal.SIGTERM, _signal_shutdown)
signal.signal(signal.SIGHUP, _signal_shutdown)

channel_to_pin = {v: k for k, v in channel_map.items()}
# Setup GPIO
GPIO.setmode(GPIO.BCM)

for pin in led_pins:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)

GPIO.setup(sample_led, GPIO.OUT)
GPIO.output(sample_led, GPIO.LOW)




# LED blink function
def blink_led(pin, duration=0.05):
    GPIO.output(pin, GPIO.HIGH)
    time.sleep(duration)
    GPIO.output(pin, GPIO.LOW)
    time.sleep(duration)

# Trigger callback
def play_and_blink(pin):
    ch = channel_map[pin]
    if engine.has_sample(ch):
        engine.trigger(ch)
        Thread(target=blink_led, args=(led_map[pin],)).start()

def rapid_blink_led(pin, stop_flag):
    while not stop_flag.is_set():
        interval = recording_blink_intervals[channel]
        GPIO.output(pin, GPIO.HIGH)
        time.sleep(interval)
        GPIO.output(pin, GPIO.LOW)
        time.sleep(interval)

def blink_while_sampling(pin):
    channel = channel_map[pin]
    stop_flag = Event()
    recording_blink_flags[channel] = stop_flag
    t = Thread(target=rapid_blink_led, args=(led_map[pin], stop_flag))
    t.daemon = True
    t.start()
    recording_blink_threads[channel] = t


def load_sounds():
    global bank_is_readonly
    engine.reload_bank(bank, BASE_DIR)
    filename = f"bank{bank}/.readonly"
    bank_is_readonly = os.path.exists(filename)
    print(f"Bank {bank} loaded. Readonly: {bank_is_readonly}")


def on_recording_completed(bank, channel):
    print(f"Recording completed for bank {bank}, channel {channel}")
    pin = list(channel_map.keys())[channel]
    stop_flag = recording_blink_flags.get(channel)
    if stop_flag:
        stop_flag.set()
    GPIO.output(led_map[pin], GPIO.LOW)
    load_sounds()

def on_recording_cancelled(bank, channel):
    print(f"Recording cancelled for bank {bank}, channel {channel}")
    pin = list(channel_map.keys())[channel]
    stop_flag = recording_blink_flags.get(channel)
    if stop_flag:
        stop_flag.set()
    GPIO.output(led_map[pin], GPIO.LOW)    

def on_sound_detected(bank, channel):
    recording_blink_intervals[channel] = 0.1

def on_sound_processing_started(bank, channel):
    print(f"Recording cancelled for bank {bank}, channel {channel}")
    pin = list(channel_map.keys())[channel]
    stop_flag = recording_blink_flags.get(channel)
    if stop_flag:
        stop_flag.set()
    GPIO.output(led_map[pin], GPIO.HIGH)  

def on_serial_package_received(package):
    global bank
    global current_mixes
    print(package)
    if package['valid']:
        if package['type'] == 'bank':
            bank = package['value']
            print(f"Bank set to {bank}")
            load_sounds()
            serial_client.send(0x00, bank)
        elif package['type'] == 'channel':
            channel = package['value']
            armed = package['armed']
            mix_value = package['mix']
            ch_idx = channel - 1
            engine.set_mix(ch_idx, (mix_value or 0) / 1023.0)
            # update current mixes and prime I2C reply
            current_mixes[ch_idx] = mix_value
        elif package['type'] == 'sampler':
            armed = package['armed']
            threshold = package['threshold']
            sampler.set_threshold(threshold / 1023.0)
            print(f"Sampler armed: {armed}, threshold: {threshold}")
    else:
        print(f"Invalid data received: {package['data']}")

serial_client.set_on_package_recieved(on_serial_package_received)
serial_client.begin()
          

sampler.set_on_recording_completed(on_recording_completed)
sampler.set_on_recording_cancelled(on_recording_cancelled)
sampler.set_on_sound_detected(on_sound_detected)
sampler.set_on_post_processing_started(on_sound_processing_started)
load_sounds()


# Main loop
try:

    print("System ready. Press buttons or send triggers.")
    while True:
        if sample_button.pressed():
            if bank_is_readonly:
                sample_mode = False
                print("Bank is read-only. Cannot enter sample mode.")
                #Thread(target=blink_led, args=(sample_led,)).start()
                blink_led(sample_led, duration=0.1)

            elif not sample_mode: #swith into sample mode
                sample_mode = True
                GPIO.output(sample_led, GPIO.HIGH)
            else: #switch out of sample mode
                sample_mode = False
                GPIO.output(sample_led, GPIO.LOW)
               

        if sample_mode:
            for pin in trigger_pins:
                if channel_buttons[pin].pressed():
                    channel = channel_map[pin]
                    if not sampler.is_armed:
                        print(f"Starting recording on bank {bank} channel {channel}.")
                        recording_blink_intervals[channel] = 0.3
                        blink_while_sampling(pin)
                        sampler.start_recording(bank, channel)
                    else:
                        sampler.cancel_recording(bank, channel)
        else:
            for pin in trigger_pins:
                if channel_buttons[pin].pressed():
                    play_and_blink(pin)
     

        time.sleep(0.005)

except KeyboardInterrupt:
    try:
        engine.stop()
    finally:
        GPIO.cleanup()
