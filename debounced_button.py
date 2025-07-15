import time
import RPi.GPIO as GPIO
GPIO.setmode(GPIO.BCM)
class DebouncedButton:
    def __init__(self, pin, debounce_time=0.05):
        self.pin = pin
        self.debounce_time = debounce_time  # seconds
        self.last_state = False
        self.last_change_time = 0
        self.state = False
        GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)

    def pressed(self):
        reading = GPIO.input(self.pin)
        now = time.time()

        if reading != self.last_state:
            self.last_change_time = now

        if (now - self.last_change_time) >= self.debounce_time:
            if reading != self.state:
                self.state = reading
                if self.state:  # Button just pressed
                    return True

        self.last_state = reading
        return False
