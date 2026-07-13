/**
 * Sequential playback queue for streamed TTS chunks.
 *
 * Chunks arrive in order (one sentence each); we play each as soon as it's
 * available, starting the next on `ended` so multi-sentence replies play
 * back-to-back while later sentences are still being synthesized.
 */
export interface AudioQueue {
  enqueue: (bytes: Uint8Array, mime: string) => void;
  /** Stop playback and drop anything still queued. */
  stop: () => void;
}

export function createAudioQueue(): AudioQueue {
  const queue: string[] = [];
  let playing = false;
  let stopped = false;
  let current: HTMLAudioElement | null = null;

  const playNext = () => {
    const url = queue.shift();
    if (!url || stopped) {
      playing = false;
      return;
    }
    playing = true;
    const el = new Audio(url);
    current = el;
    const advance = () => {
      URL.revokeObjectURL(url);
      playNext();
    };
    el.onended = advance;
    el.onerror = advance;
    el.play().catch(advance);
  };

  return {
    enqueue(bytes: Uint8Array, mime: string) {
      if (stopped) return;
      const url = URL.createObjectURL(new Blob([bytes.buffer as ArrayBuffer], { type: mime }));
      queue.push(url);
      if (!playing) playNext();
    },
    stop() {
      stopped = true;
      current?.pause();
      current = null;
      queue.splice(0).forEach((url) => URL.revokeObjectURL(url));
      playing = false;
    },
  };
}
