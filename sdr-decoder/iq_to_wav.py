#!/usr/bin/env python3
import sys
import wave
import numpy as np
from scipy.signal import decimate

def main():
    if len(sys.argv) != 5:
        print(f"uso: {sys.argv[0]} <in.cu8> <out.wav> <capture_rate_hz> <freq_correction_hz>")
        sys.exit(1)

    in_path = sys.argv[1]
    out_path = sys.argv[2]
    capture_rate = float(sys.argv[3])
    freq_corr = float(sys.argv[4])

    raw = np.fromfile(in_path, dtype=np.uint8)
    raw = raw[: len(raw) - (len(raw) % 2)]
    iq = raw.astype(np.float32)
    iq = (iq - 127.5) / 127.5
    i = iq[0::2]
    q = iq[1::2]
    signal = i + 1j * q

    n = np.arange(len(signal), dtype=np.float64)
    if freq_corr != 0.0:
        rotator = np.exp(-1j * 2.0 * np.pi * freq_corr * n / capture_rate)
        signal = signal * rotator.astype(np.complex64)

    prod = signal[1:] * np.conj(signal[:-1])
    demod = np.angle(prod).astype(np.float32)

    decim = int(round(capture_rate / 48000.0))
    if decim > 1:
        audio = decimate(demod, decim, ftype="fir", zero_phase=True)
    else:
        audio = demod

    peak = np.max(np.abs(audio))
    if peak < 1e-9:
        peak = 1.0
    scaled = (audio / peak) * (0.7 * 32767.0)
    pcm = scaled.astype(np.int16)

    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(pcm.tobytes())

    print(f"OK: {out_path} ({len(pcm)/48000:.1f}s, pico_pre_norm={peak:.4f} rad/muestra, corr={freq_corr:+.0f}Hz)")

if __name__ == "__main__":
    main()
