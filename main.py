import pygame
import RPi.GPIO as GPIO
import time
from threading import Thread, Event
from debounced_button import DebouncedButton
from sampler import Sampler
from serial_client import SerialClient

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
recording_blink_threads = {}
recording_blink_flags = {}

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
serial_client = SerialClient(device='/dev/ttyUSB0')



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

# Trigger callback
def play_and_blink(pin):
    sounds[pin].play()
    Thread(target=blink_led, args=(led_map[pin],)).start()

def rapid_blink_led(pin, stop_flag, interval=0.1):
    while not stop_flag.is_set():
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
    sounds = {
        pin: pygame.mixer.Sound(f"bank{bank}/sound{channel_map[pin]}.wav")
        for pin in sound_files
    }

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

def on_serial_package_received(package):
    print(package)
    if package['valid']:
        if package['type'] == 'bank':
            bank = package['value']
            print(f"Bank set to {bank}")
            load_sounds()
        elif package['type'] == 'channel':
            channel = package['value']
            armed = package['armed']
            mix_value = package['mix']
            print(f"Channel {channel} armed: {armed}, mix value: {mix_value}")
        elif package['type'] == 'sampler':
            armed = package['armed']
            threshold = package['threshold']
            print(f"Sampler armed: {armed}, threshold: {threshold}")
    else:
        print(f"Invalid data received: {package['data']}")

serial_client.set_on_package_recieved(on_serial_package_received)
#serial.begin()

sampler.set_on_recording_completed(on_recording_completed)
sampler.set_on_recording_cancelled(on_recording_cancelled)
load_sounds()



# Main loop
try:

    print("System ready. Press buttons or send triggers.")
    while True:
        if sample_button.pressed():
            if not sample_mode: #swith into sample mode
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
                        bank = 1
                        print(f"Starting recording on bank {bank} channel {channel}.")
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
