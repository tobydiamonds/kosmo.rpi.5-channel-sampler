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
        self.rms = 0
        self.armed = False
        self._on_recording_completed = None
        self._on_recodring_cancelled = None
    
    def set_on_recording_completed(self, callback):
        self._on_recording_completed = callback

    def _fire_recording_completed(self):
        if self._on_recording_completed is not None:
            self._on_recording_completed(self.current_bank, self.current_channel)  

    def set_on_recording_cancelled(self, callback):      
        self._on_recodring_cancelled = callback

    def _fire_recording_cancelled(self):
        if self._on_recodring_cancelled is not None:
            self._on_recodring_cancelled(self.current_bank, self.current_channel)  

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
                buf = np.concatenate(list(self.audio_queue.queue))
                self.rms = np.sqrt(np.mean(buf**2))

    # def get_rms(self):
    #     if not self.audio_queue.empty():
    #         buf = np.concatenate(list(self.audio_queue.queue))
    #         return np.sqrt(np.mean(buf**2))        
    #     else:
    #         return 0.0

    def button_monitor(self):
        """Start recording when input exceeds threshold, stop when it drops below, with cooldown and timeout."""
        recfile = None
        is_recording = False
        last_stop_time = 0
 
        while not self.shutdown.is_set():
            now = time.time()
           #rms = self.get_rms()
           #print(is_recording, self.rms)

            if self.rms > THRESHOLD and not is_recording and (now - last_stop_time) > COOLDOWN_SEC:
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

            if is_recording:
                # stop if sound drops below threshold (immediate stop)
                if self.rms < THRESHOLD:
                    print("Sound dropped below threshold – recording stopped.")
                    self.stop_recording()
                    
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

    def cancel_recording(self, bank, channel):
        """Cancel the current recording and reset state."""

        if bank != self.current_bank or channel != self.current_channel:
            return

        if self.recfile:
            self.recfile.close()
            self.recfile = None
        self.preroll_queue.queue.clear()
        self.audio_queue.queue.clear()
        self.recording.clear()
        self.shutdown.set()
        self.armed = False
        self._fire_recording_cancelled()

    def stop_recording(self):
        """Stop recording and reset state."""
        filename = None
        if self.recfile:
            filename = self.recfile.name
            self.recfile.close()
            self.recfile = None
        self.preroll_queue.queue.clear()
        self.audio_queue.queue.clear()            
        self.recording.clear()
        self.shutdown.set()
        self.armed = False


        time.sleep(1) # allow threads to exit gracefully
        if not filename is None:
            self.trim_silence(filename, threshold=THRESHOLD)    

        self._fire_recording_completed()    


    def start_recording(self, bank, channel):
        if bank < 0:
            raise ValueError("Invalid bank selected.")
        self.current_bank = bank

        if channel < 0 or channel > 4   :
            raise ValueError("Invalid channel selected.")
        self.current_channel = channel

        self.armed = True

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
    def is_armed(self):
        return self.armed
        
 


