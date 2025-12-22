import pygame
import os
import RPi.GPIO as GPIO
import time
from threading import Thread, Event
from debounced_button import DebouncedButton
from sampler import Sampler
from serial_client import SerialClient
from i2c_slave import I2CSlave

# Setup
pygame.mixer.init()

# GPIO pin configuration
trigger_pins = [17,22,24,5,12]
led_pins = [27,23,25,6,13]
sample_pin = 19
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
i2c_slave = I2CSlave(address=0x0A)



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
    if pin in sounds:
        sounds[pin].stop()
        sounds[pin].play()
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
    global sounds
    global bank_is_readonly
    sounds = {}
    for pin in sound_files:
        filename = f"bank{bank}/sound{channel_map[pin]}.wav"
        if os.path.exists(filename):
            sounds[pin] = pygame.mixer.Sound(filename)
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
            i2c_slave.set_reply_payload(bank, current_mixes)
        elif package['type'] == 'channel':
            channel = package['value']
            armed = package['armed']
            mix_value = package['mix']
            pin = list(channel_map.keys())[channel-1]
            if pin in sounds:
                sounds[pin].set_volume(mix_value / 1023.0)
            # update current mixes and prime I2C reply
            current_mixes[channel - 1] = mix_value
            i2c_slave.set_reply_payload(bank, current_mixes)
        elif package['type'] == 'sampler':
            armed = package['armed']
            threshold = package['threshold']
            sampler.set_threshold(threshold / 1023.0)
            print(f"Sampler armed: {armed}, threshold: {threshold}")
    else:
        print(f"Invalid data received: {package['data']}")

serial_client.set_on_package_recieved(on_serial_package_received)
serial_client.begin()

def on_i2c_payload_received(payload):
    global bank
    global current_mixes
    new_bank = int(payload.get('bank', bank))
    mixes = payload.get('mixes', [])
    if new_bank != bank:
        bank = new_bank
        print(f"[I2C] Bank set to {bank}")
        load_sounds()
        serial_client.send(0x00, bank)
        i2c_slave.set_reply_payload(bank, current_mixes)

    if mixes and len(mixes) == 5:
        for ch, mix_val in enumerate(mixes):
            pin = channel_to_pin.get(ch)
            if pin is not None and pin in sounds:
                vol = max(0.0, min(1.0, (mix_val or 0) / 1023.0))
                sounds[pin].set_volume(vol)
            address = 0x01 + ch
            serial_client.send(address, int(mix_val))
        # store and prime reply
        current_mixes = [int(m) for m in mixes]
        i2c_slave.set_reply_payload(bank, current_mixes)
            

i2c_slave.set_on_payload_received(on_i2c_payload_received)
try:
    i2c_slave.begin()
    print("I2C slave started at address 0x0A")
except Exception as e:
    print(f"Failed to start I2C slave: {e}")

sampler.set_on_recording_completed(on_recording_completed)
sampler.set_on_recording_cancelled(on_recording_cancelled)
sampler.set_on_sound_detected(on_sound_detected)
sampler.set_on_post_processing_started(on_sound_processing_started)
load_sounds()
i2c_slave.set_reply_payload(bank, current_mixes)


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
    GPIO.cleanup()
    try:
        i2c_slave.end()
    except Exception:
        pass
