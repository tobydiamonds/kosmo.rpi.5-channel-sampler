import pygame
import RPi.GPIO as GPIO
import time
from threading import Thread
from debounced_button import DebouncedButton
from sampler import Sampler

# Setup
pygame.mixer.init()

# GPIO pin configuration
trigger_pins = [17,22,24,5,12]
led_pins = [27,23,25,6,13]
sample_pin = 19
sample_led = 16
sample_mode = False
bank = 0

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
def play_and_blink(channel):
    sounds[channel].play()
    Thread(target=blink_led, args=(led_map[channel],)).start()

def load_sounds():
    global sounds
    sounds = {
        pin: pygame.mixer.Sound(f"bank{bank}/sound{channel_map[pin]}.wav")
        for pin in sound_files
    }

load_sounds()


# Main loop
try:

    print("System ready. Press buttons or send triggers.")
    while True:
        if sample_button.pressed():
            if not sample_mode: #swith into sample mode
                sample_mode = True
                GPIO.output(sample_led, GPIO.HIGH)
                print("Sample mode:", "ON")
            else: #switch out of sample mode
                sample_mode = False
                GPIO.output(sample_led, GPIO.LOW)
                print("Sample mode:", "OFF")
                # Load sounds into memory
                load_sounds()
                

        if sample_mode:
            for pin in trigger_pins:
                if channel_buttons[pin].pressed():
                    print(f"Trigger {pin} pressed in sample mode.")
                    channel = channel_map[pin]
                    if not sampler.sampler_is_recording:
                        bank = 1
                        print(f"Starting recording on bank {bank} channel {channel}.")
                        sampler.start_recording(bank, channel)
                        

        else:


            for pin in trigger_pins:
                if channel_buttons[pin].pressed():
                    play_and_blink(pin)
     

        time.sleep(0.005)

except KeyboardInterrupt:
    GPIO.cleanup()
