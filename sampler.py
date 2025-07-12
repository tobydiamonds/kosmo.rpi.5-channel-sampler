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
SAVE_DIR        = '/home/pi/samples'  # on SD card
COOLDOWN_SEC    = 0.1         # seconds to wait after stopping before re-arming
# -----------------------------------

class Sampler:
    def __init__(self):
        os.makedirs(SAVE_DIR, exist_ok=True)

        self.audio_q = queue.Queue(maxsize=int(PRE_ROLL_SEC * SAMPLE_RATE))
        self.recording = threading.Event()
        self.shutdown = threading.Event()
        self.current_channel = -1  # no channel selected

    def audio_thread(self):
        """Continuously read audio into a ring buffer and disk when recording."""
        recfile = None
        stream = sd.InputStream(samplerate=SAMPLE_RATE,
                                channels=CHANNELS,
                                device=DEVICE_INDEX,
                                blocksize=1024)
        with stream:
            for block in stream:
                if shutdown.is_set():
                    break
                # Keep a small pre-roll
                if not recording.is_set():
                    if audio_q.full():
                        audio_q.get_nowait()
                    audio_q.put_nowait(block.copy())
                else:
                    recfile.write(block)

    def button_monitor(self):
        """Start recording when input exceeds threshold, stop when it drops below, with cooldown."""
        recfile = None
        is_recording = False
        last_stop_time = 0

        while not self.shutdown.is_set():
            now = time.time()
            if not self.audio_q.empty():
                buf = np.concatenate(list(self.audio_q.queue))
                rms = np.sqrt(np.mean(buf**2))

                if rms > THRESHOLD and not is_recording and (now - last_stop_time) > COOLDOWN_SEC:
                    # Start recording
                    ts = time.strftime('%Y%m%d_%H%M%S')
                    #path = os.path.join(SAVE_DIR, f"sample_{ts}.wav")
                    path = os.path.join(SAVE_DIR, f"sound{self.current_channel}_{ts}.wav")
                    recfile = sf.SoundFile(path, mode='w',
                                           samplerate=SAMPLE_RATE,
                                           channels=CHANNELS,
                                           subtype='PCM_16')
                    print(f"Sound detected! Recording to {path}")
                    # dump pre-roll
                    for b in list(self.audio_q.queue):
                        recfile.write(b)
                    self.audio_q.queue.clear()
                    self.recording.set()
                    is_recording = True

                elif rms <= THRESHOLD and is_recording:
                    # Stop recording
                    self.recording.clear()
                    recfile.close()
                    print("Sound dropped below threshold – recording stopped.")
                    is_recording = False
                    last_stop_time = now

            time.sleep(0.01)

    def start_recording(self, channel):
        if channel < 0 or channel > 4   :
            raise ValueError("Invalid channel selected.")
        self.current_channel = channel
        """Start the audio thread and button monitor."""
        self.shutdown.clear()
        self.recording.clear()
        threading.Thread(target=self.audio_thread, daemon=True).start()
        threading.Thread(target=self.button_monitor, daemon=True).start()
        print("Sampler is armed. Input audio above threshold to start sampling...")

    @property
    def is_recording(self):
        """Returns True if currently recording, False otherwise."""
        return self.recording.is_set()
        
 


