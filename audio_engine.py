import os
import time
import numpy as np
import sounddevice as sd
import soundfile as sf
from threading import Lock
from typing import Optional, List


class AudioEngine:
    def __init__(self,
                 samplerate: int = 48000,
                 device_name_hint: Optional[str] = None,
                 main_gain: float = 0.7,
                 direct_gain: Optional[float] = None,
                 blocksize: Optional[int] = None,
                 latency: Optional[float] = None):
        self.samplerate = samplerate
        self.device_name_hint = device_name_hint or os.environ.get('AUDIO_DEVICE_NAME', 'UMC1820')
        self.main_gain = main_gain
        # Direct outs global gain; default via env or 0.7 to keep headroom
        env_direct = os.environ.get('AUDIO_DIRECT_GAIN')
        self.direct_gain = (
            float(env_direct) if env_direct is not None else (1.0 if direct_gain is None else float(direct_gain))
        )
        # Lower blocksize reduces retrigger latency (in frames). Default 256.
        env_bs = os.environ.get('AUDIO_BLOCKSIZE')
        try:
            self.blocksize = int(env_bs) if env_bs is not None else (256 if blocksize is None else int(blocksize))
        except Exception:
            self.blocksize = 256
        # Optional target latency in seconds (backend dependent). If not set, let backend decide.
        env_lat = os.environ.get('AUDIO_LATENCY')
        try:
            self.latency = float(env_lat) if env_lat is not None else (None if latency is None else float(latency))
        except Exception:
            self.latency = None

        # 5 logical channels (0..4). Hardware output mapping:
        # 0: Main L, 1: Main R, 2..6: Direct outs for samples 0..4
        self.hw_channels = 7
        self.sample_buffers: List[Optional[np.ndarray]] = [None] * 5  # store mono float32 arrays
        self.playheads: List[Optional[int]] = [None] * 5
        # Default mixes to 1.0 so main L/R are audible until overridden
        self.mixes: List[float] = [1.0] * 5  # 0..1, affects main mix only
        self._lock = Lock()
        # Optional per-channel overrides for direct outs (0..4); None uses global direct_gain
        self.direct_gains: List[Optional[float]] = [None] * 5

        self.stream: Optional[sd.OutputStream] = None

    def _find_output_device(self) -> Optional[int]:
        try:
            devices = sd.query_devices()
        except Exception:
            return None
        # Prefer name hint with enough channels; else first with enough channels
        best = None
        for idx, d in enumerate(devices):
            try:
                ch = int(d.get('max_output_channels', 0))
                name = str(d.get('name', ''))
            except Exception:
                continue
            if ch >= self.hw_channels and (self.device_name_hint.lower() in name.lower()):
                best = idx
                break
            if ch >= self.hw_channels and best is None:
                best = idx
        return best

    def start(self):
        if self.stream is not None:
            return
        # List available output devices for diagnostics
        try:
            print('[AudioEngine] Available devices:')
            for idx, d in enumerate(sd.query_devices()):
                name = d.get('name', '')
                max_out = d.get('max_output_channels', 0)
                print(f"  {idx}: {name} (max_out={max_out})")
        except Exception as e:
            print(f"[AudioEngine] Could not list devices: {e}")
        device_index = self._find_output_device()
        kwargs = dict(
            samplerate=self.samplerate,
            device=device_index,
            channels=self.hw_channels,
            dtype='float32',
            blocksize=self.blocksize,
            callback=self._callback,
        )
        if self.latency is not None:
            kwargs['latency'] = self.latency
        self.stream = sd.OutputStream(**kwargs)
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            try:
                # Abort first to avoid waiting for buffer drain
                try:
                    self.stream.abort()
                except Exception:
                    pass
                try:
                    self.stream.stop()
                except Exception:
                    pass
                try:
                    self.stream.close()
                except Exception:
                    pass
                # Brief sleep to allow backend to release the device
                try:
                    sd.sleep(50)
                except Exception:
                    time.sleep(0.05)
                # Global best-effort release of PortAudio state
                try:
                    sd.stop()
                except Exception:
                    pass
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception:
                    pass
            finally:
                self.stream = None

    def __del__(self):
        # Best-effort release of audio device on object destruction
        try:
            self.stop()
        except Exception:
            pass

    def _callback(self, outdata, frames, time_info, status):
        # outdata shape: (frames, hw_channels)
        out = np.zeros((frames, self.hw_channels), dtype=np.float32)
        with self._lock:
            for ch in range(5):
                buf = self.sample_buffers[ch]
                head = self.playheads[ch]
                if buf is None or head is None:
                    continue

                remaining = len(buf) - head
                if remaining <= 0:
                    self.playheads[ch] = None
                    continue

                n = min(frames, remaining)
                seg = buf[head:head + n]
                # Direct out (mono): channel index 2 + ch
                direct_idx = 2 + ch
                dg = self.direct_gains[ch]
                g = float(self.direct_gain if dg is None else dg)
                out[:n, direct_idx] += seg * g
                # Main stereo mix, apply mix value
                vol = float(self.mixes[ch]) * self.main_gain
                if vol > 0:
                    out[:n, 0] += seg * vol
                    out[:n, 1] += seg * vol

                self.playheads[ch] = head + n
                if self.playheads[ch] >= len(buf):
                    self.playheads[ch] = None

        # Prevent clipping
        np.clip(out, -1.0, 1.0, out=out)
        outdata[:] = out

    def reload_bank(self, bank: int, base_path: str):
        # Load bank{bank}/sound{i}.wav files as mono float32 @ self.samplerate
        new_buffers: List[Optional[np.ndarray]] = [None] * 5
        for i in range(5):
            path = os.path.join(base_path, f"bank{bank}", f"sound{i}.wav")
            if not os.path.exists(path):
                new_buffers[i] = None
                continue
            try:
                # Read as 2D to handle multi-channel consistently, then convert to mono
                data, sr = sf.read(path, dtype='float32', always_2d=True)
                if data.ndim == 2:
                    data = data.mean(axis=1)
                # Ensure 1D float32
                data = np.asarray(data, dtype=np.float32)
                if data.size == 0:
                    new_buffers[i] = None
                    continue
                # Resample only after making it mono
                if sr != self.samplerate:
                    ratio = self.samplerate / float(sr)
                    x_old = np.arange(len(data), dtype=np.float32)
                    x_new = np.arange(0, len(data) * ratio, 1.0, dtype=np.float32)
                    data = np.interp(x_new, x_old, data).astype(np.float32)
                new_buffers[i] = data
            except Exception:
                # If any single file fails to load/resample, skip it instead of crashing
                new_buffers[i] = None

        with self._lock:
            self.sample_buffers = new_buffers
            # Do not reset mixes or playheads here

    def has_sample(self, channel: int) -> bool:
        with self._lock:
            buf = self.sample_buffers[channel] if 0 <= channel < 5 else None
            return buf is not None and len(buf) > 0

    def trigger(self, channel: int):
        if not (0 <= channel < 5):
            return
        with self._lock:
            if self.sample_buffers[channel] is None:
                return
            self.playheads[channel] = 0

    def set_mix(self, channel: int, value01: float):
        if not (0 <= channel < 5):
            return
        v = max(0.0, min(1.0, float(value01)))
        with self._lock:
            self.mixes[channel] = v

    def set_direct_gain_global(self, value: float):
        with self._lock:
            self.direct_gain = float(value)

    def set_direct_gain(self, channel: int, value: Optional[float]):
        if not (0 <= channel < 5):
            return
        with self._lock:
            self.direct_gains[channel] = None if value is None else float(value)

