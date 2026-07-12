import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useMic } from './useMic';

// jsdom has no MediaRecorder or Web Audio, so we install controllable fakes.
class FakeMediaRecorder {
  static isTypeSupported = () => false;
  state: 'inactive' | 'recording' = 'inactive';
  mimeType = 'audio/webm';
  ondataavailable: ((e: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  start(): void {
    this.state = 'recording';
  }
  stop(): void {
    this.state = 'inactive';
    this.onstop?.();
  }
}

// Mic level the fake analyser reports (RMS of a constant buffer == |level|).
let micLevel = 0;

class FakeAnalyser {
  fftSize = 2048;
  connect(): void {}
  getFloatTimeDomainData(arr: Float32Array): void {
    arr.fill(micLevel);
  }
}

class FakeAudioContext {
  createMediaStreamSource() {
    return { connect: () => {} };
  }
  createAnalyser() {
    return new FakeAnalyser();
  }
  resume() {
    return Promise.resolve();
  }
  close() {
    return Promise.resolve();
  }
}

function installAudioContext(): void {
  vi.stubGlobal('AudioContext', FakeAudioContext);
}

beforeEach(() => {
  micLevel = 0;
  vi.stubGlobal('MediaRecorder', FakeMediaRecorder);
  vi.stubGlobal('navigator', {
    mediaDevices: {
      getUserMedia: vi.fn().mockResolvedValue({ getTracks: () => [] }),
    },
  });
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('useMic auto-stop (silence detection)', () => {
  it('stops after a silence gap that follows speech', async () => {
    installAudioContext();
    const onStop = vi.fn();
    const { result } = renderHook(() => useMic({ onStop, autoStopMs: 2000 }));

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.state).toBe('recording');

    // Calibrate on silence, then speak, then go quiet.
    await act(async () => {
      vi.advanceTimersByTime(500); // finish 400ms calibration (silence)
    });
    micLevel = 0.2; // speaking
    await act(async () => {
      vi.advanceTimersByTime(600);
    });
    expect(result.current.state).toBe('recording'); // still talking
    micLevel = 0; // silence begins
    await act(async () => {
      vi.advanceTimersByTime(2100); // > 2000ms silence window
    });

    expect(result.current.state).toBe('idle');
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('keeps recording through continuous speech (does not stop at the window)', async () => {
    installAudioContext();
    const onStop = vi.fn();
    const { result } = renderHook(() => useMic({ onStop, autoStopMs: 2000 }));

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      vi.advanceTimersByTime(500);
    });
    micLevel = 0.2; // never goes quiet
    await act(async () => {
      vi.advanceTimersByTime(10_000); // way past the 2s window, under the 60s cap
    });

    expect(result.current.state).toBe('recording');
    expect(onStop).not.toHaveBeenCalled();
  });

  it('does not fire the old "N seconds after start" behavior', async () => {
    installAudioContext();
    const onStop = vi.fn();
    const { result } = renderHook(() => useMic({ onStop, autoStopMs: 2000 }));

    await act(async () => {
      await result.current.start();
    });
    micLevel = 0.2; // talking the whole time
    await act(async () => {
      vi.advanceTimersByTime(2500); // past the window, but still speaking
    });

    // The bug would have stopped ~2s after start; VAD must keep going.
    expect(result.current.state).toBe('recording');
    expect(onStop).not.toHaveBeenCalled();
  });

  it('without Web Audio, falls back to the max-length cap (not the silence window)', async () => {
    // No AudioContext stubbed → fallback path.
    const onStop = vi.fn();
    const { result } = renderHook(() => useMic({ onStop, autoStopMs: 2000 }));

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      vi.advanceTimersByTime(5000); // past the 2s window
    });
    expect(result.current.state).toBe('recording'); // not stopped by silence window

    await act(async () => {
      vi.advanceTimersByTime(60_000); // hit the hard cap
    });
    expect(result.current.state).toBe('idle');
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('autoStopMs of 0 disables auto-stop entirely', async () => {
    installAudioContext();
    const onStop = vi.fn();
    const { result } = renderHook(() => useMic({ onStop, autoStopMs: 0 }));

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      vi.advanceTimersByTime(120_000);
    });
    expect(result.current.state).toBe('recording');
    expect(onStop).not.toHaveBeenCalled();
  });

  it('manual stop cancels auto-stop (onStop fires once)', async () => {
    installAudioContext();
    const onStop = vi.fn();
    const { result } = renderHook(() => useMic({ onStop, autoStopMs: 2000 }));

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      result.current.stop();
    });
    expect(onStop).toHaveBeenCalledTimes(1);

    // No lingering timer/poller fires a second stop.
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});
