import { useCallback, useEffect, useState } from 'react';
import {
  PROFILE_STORAGE_KEY,
  clearStoredProfileId,
  getStoredProfileId,
  setStoredProfileId,
} from '../api/client';
import { getUser, type User } from '../api/users';

interface ProfileState {
  status: 'loading' | 'unselected' | 'ready' | 'error';
  user: User | null;
  error: string | null;
}

interface UseProfile extends ProfileState {
  selectProfile: (id: number) => void;
  clearProfile: () => void;
  refresh: () => void;
}

export function useProfile(): UseProfile {
  const [state, setState] = useState<ProfileState>({
    status: 'loading',
    user: null,
    error: null,
  });
  const [tick, setTick] = useState(0);

  // Watch for changes from other tabs.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === PROFILE_STORAGE_KEY) setTick((n) => n + 1);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const id = getStoredProfileId();
    if (id === null) {
      setState({ status: 'unselected', user: null, error: null });
      return;
    }
    // Only flip into the loading state on the very first load. When a refresh
    // happens after a settings change, we keep the previous user visible so
    // the page tree (and scroll position) doesn't reset.
    setState((prev) =>
      prev.user !== null ? prev : { status: 'loading', user: null, error: null },
    );
    getUser(id)
      .then((user) => {
        if (cancelled) return;
        setState({ status: 'ready', user, error: null });
      })
      .catch((err: Error) => {
        if (cancelled) return;
        // The stored profile no longer exists or backend is unreachable.
        clearStoredProfileId();
        setState({ status: 'error', user: null, error: err.message });
      });
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const selectProfile = useCallback((id: number) => {
    setStoredProfileId(id);
    setTick((n) => n + 1);
  }, []);

  const clearProfile = useCallback(() => {
    clearStoredProfileId();
    setTick((n) => n + 1);
  }, []);

  const refresh = useCallback(() => setTick((n) => n + 1), []);

  return { ...state, selectProfile, clearProfile, refresh };
}
