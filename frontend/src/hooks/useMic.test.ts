import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useMic } from './useMic';

// A minimal MediaRecorder stand-in. jsdom has no MediaRecorder, so we install
// one that lets us drive start/stop and the onstop callback by hand.
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

beforeEach(() => {
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

describe('useMic auto-stop', () => {
  it('stops on its own after autoStopMs and fires onStop', async () => {
    const onStop = vi.fn();
    const { result } = renderHook(() => useMic({ onStop, autoStopMs: 7000 }));

    await act(async () => {
      await result.current.start();
    });
    expect(result.current.state).toBe('recording');
    expect(onStop).not.toHaveBeenCalled();

    // Advance to the auto-stop deadline.
    await act(async () => {
      vi.advanceTimersByTime(7000);
    });
    expect(result.current.state).toBe('idle');
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('does not auto-stop when autoStopMs is 0', async () => {
    const onStop = vi.fn();
    const { result } = renderHook(() => useMic({ onStop, autoStopMs: 0 }));

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(result.current.state).toBe('recording');
    expect(onStop).not.toHaveBeenCalled();
  });

  it('manual stop cancels the pending auto-stop (onStop fires once)', async () => {
    const onStop = vi.fn();
    const { result } = renderHook(() => useMic({ onStop, autoStopMs: 7000 }));

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      result.current.stop();
    });
    expect(onStop).toHaveBeenCalledTimes(1);

    // The previously-armed timer must not fire a second stop.
    await act(async () => {
      vi.advanceTimersByTime(7000);
    });
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});
