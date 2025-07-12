import os, time, queue, threading, numpy as np
import RPi.GPIO as GPIO
import sounddevice as sd
import soundfile as sf

# ---------- SETTINGS -------------
THRESHOLD       = 0.05        # RMS amplitude (0–1) above noise floor
PRE_ROLL_SEC    = 0.25        # audio kept before threshold crossing
SAMPLE_RATE     = 48000
CHANNELS        = 1
DEVICE_INDEX    = 1           # adjust to match your USB mic
SAVE_DIR        = '/home/pi/kosmo-5ch-sampler/'  # on SD card
COOLDOWN_SEC    = 0.1         # seconds to wait after stopping before re-arming
TIMEOUT_SEC     = 0.5         # Stop recording if silence lasts this long
# -----------------------------------

class Sampler:
    def __init__(self):
        self.preroll_queue = queue.Queue(maxsize=int(PRE_ROLL_SEC * SAMPLE_RATE))
        self.audio_queue = queue.Queue(maxsize=int(PRE_ROLL_SEC * SAMPLE_RATE))
        self.recording = threading.Event()
        self.shutdown = threading.Event()
        self.current_bank = -1
        self.current_channel = -1  # no channel selected
        self.recfile = None

    def audio_thread(self):
        """Continuously read audio into a ring buffer and disk when recording."""
        stream = sd.InputStream(samplerate=SAMPLE_RATE,
                                channels=CHANNELS,
                                device=DEVICE_INDEX,
                                blocksize=1024)
        with stream:
            while not self.shutdown.is_set():
                block, _ = stream.read(1024)
                # Keep a small pre-roll
                if not self.recording.is_set():
                    if self.preroll_queue.full():
                        self.preroll_queue.get_nowait()
                    self.preroll_queue.put_nowait(block.copy())
                else:
                    if self.recfile:  # Only write if recfile is set
                        self.recfile.write(block)
                self.audio_queue.put_nowait(block.copy())

    def get_rms(self):
        if not self.audio_queue.empty():
            buf = np.concatenate(list(self.audio_queue.queue))
            return np.sqrt(np.mean(buf**2))        
        else:
            return 0.0

    def button_monitor(self):
        """Start recording when input exceeds threshold, stop when it drops below, with cooldown and timeout."""
        recfile = None
        is_recording = False
        last_stop_time = 0
        silence_start_time = None
 
        while not self.shutdown.is_set():
            now = time.time()
            rms = self.get_rms()
            print(is_recording, rms)

            if rms > THRESHOLD and not is_recording and (now - last_stop_time) > COOLDOWN_SEC:
                # Start recording
                

                path = os.path.join(SAVE_DIR, f"bank{self.current_bank}/sound{self.current_channel}.wav")
                self.recfile = sf.SoundFile(path, mode='w',
                                        samplerate=SAMPLE_RATE,
                                        channels=CHANNELS,
                                        subtype='PCM_16')
                print(f"Sound detected! Recording to {path}")
                # dump pre-roll
                for b in list(self.preroll_queue.queue):
                    self.recfile.write(b)
                self.preroll_queue.queue.clear()
                self.recording.set()
                is_recording = True
                silence_start_time = None  # Reset silence timer

            if is_recording:
                if rms > THRESHOLD:
                    silence_start_time = None  # Reset silence timer if sound returns
                else:
                    if silence_start_time is None:
                        silence_start_time = now
                    elif now - silence_start_time > TIMEOUT_SEC:
                        # Stop recording due to timeout
                        self.stop_recording()
                        print("Silence timeout – recording stopped.")
                        is_recording = False
                        last_stop_time = now
                        silence_start_time = None
                # Also stop if sound drops below threshold (immediate stop)
                if rms <= THRESHOLD and silence_start_time is None:
                    self.stop_recording()
                    print("Sound dropped below threshold – recording stopped.")
                    is_recording = False
                    last_stop_time = now

            time.sleep(0.01)

    def trim_silence(self, filename, threshold=THRESHOLD):
        """Trim leading and trailing silence from a WAV file.
        The end threshold is 0.025 lower than the start threshold to avoid abrupt cuts."""
        data, samplerate = sf.read(filename)
        if data.ndim > 1:
            mono = np.mean(data, axis=1)
        else:
            mono = data

        # Start: use the normal threshold
        start_mask = np.abs(mono) > threshold
        if not np.any(start_mask):
            print("No audio above threshold found.")
            return

        start = np.argmax(start_mask)

        # End: use a slightly lower threshold
        end_threshold = max(0, threshold - 0.025)
        end_mask = np.abs(mono) > end_threshold
        if not np.any(end_mask):
            end = len(mono)
        else:
            end = len(end_mask) - np.argmax(end_mask[::-1])

        trimmed = data[start:end]
        sf.write(filename, trimmed, samplerate)
        print(f"Trimmed silence: {filename} [{start}:{end}] (end threshold: {end_threshold})")        

    def stop_recording(self):
        """Stop recording and reset state."""
        if self.recfile:
            self.recfile.close()

            self.trim_silence(self.recfile.name, threshold=THRESHOLD)

            self.recfile = None
        self.recording.clear()
        self.shutdown.set()
        self.current_channel = -1  # Reset channel
        print("Recording stopped.")


    def start_recording(self, bank, channel):
        if bank < 0:
            raise ValueError("Invalid bank selected.")
        self.current_bank = bank

        if channel < 0 or channel > 4   :
            raise ValueError("Invalid channel selected.")
        self.current_channel = channel

        """ensure the bank folder exists"""
        bank_folder = os.path.join(SAVE_DIR, f"bank{self.current_bank}")
        os.makedirs(bank_folder, exist_ok=True)

        """Start the audio thread and button monitor."""
        self.shutdown.clear()
        self.recording.clear()
        threading.Thread(target=self.audio_thread, daemon=True).start()
        threading.Thread(target=self.button_monitor, daemon=True).start()
        print("Sampler is armed. Input audio above threshold to start sampling...")

    @property
    def sampler_is_recording(self):
        """Returns True if currently recording, False otherwise."""
        return self.recording.is_set()
        
 


