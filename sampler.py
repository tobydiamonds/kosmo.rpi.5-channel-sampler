import pygame
import RPi.GPIO as GPIO
import time
from threading import Thread
from debounced_button import DebouncedButton

# Setup
pygame.mixer.init()

# GPIO pin configuration
trigger_pins = [17,22,24,5,12]
led_pins = [27,23,25,6,13]

# Map triggers to sounds and LEDs
sound_files = {
    17: "bank0/sound0.wav",
    22: "bank0/sound1.wav"
}
led_map = {
    17: 27,
    22: 23,
    24: 25,
    5: 6,
    12: 13
}

DEBOUNCE_TIME = 0.001


channel_buttons = {
    17: DebouncedButton(pin=17, debounce_time=DEBOUNCE_TIME),
    22: DebouncedButton(pin=22, debounce_time=DEBOUNCE_TIME),
    24: DebouncedButton(pin=24, debounce_time=DEBOUNCE_TIME),
    5: DebouncedButton(pin=5, debounce_time=DEBOUNCE_TIME),
    12: DebouncedButton(pin=12, debounce_time=DEBOUNCE_TIME)
}




# Load sounds into memory
sounds = {pin: pygame.mixer.Sound(file) for pin, file in sound_files.items()}

# Setup GPIO
GPIO.setmode(GPIO.BCM)

for pin in led_pins:
    GPIO.setup(pin, GPIO.OUT)
    GPIO.output(pin, GPIO.LOW)

# LED blink function
def blink_led(pin, duration=0.05):
    GPIO.output(pin, GPIO.HIGH)
    time.sleep(duration)
    GPIO.output(pin, GPIO.LOW)

# Trigger callback
def play_and_blink(channel):
    sounds[channel].play()
    Thread(target=blink_led, args=(led_map[channel],)).start()

# Main loop
try:
    print("System ready. Press buttons or send triggers.")
    while True:
        for pin in trigger_pins:
            if channel_buttons[pin].pressed():
                play_and_blink(pin)

        time.sleep(0.005)

except KeyboardInterrupt:
    GPIO.cleanup()
