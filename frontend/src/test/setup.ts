import '@testing-library/jest-dom';

// Node 22+ ships an experimental global `localStorage` (Web Storage API) that
// is a non-functional stub unless the process is started with
// `--localstorage-file`. In the jsdom test environment this stub shadows
// jsdom's own working localStorage, so `localStorage.setItem` is undefined and
// every test that touches storage throws. Install a simple in-memory Storage
// so the suite is independent of the Node version's Web Storage behavior.
function createMemoryStorage(): Storage {
  const store = new Map<string, string>();
  return {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    removeItem: (key: string) => {
      store.delete(key);
    },
    setItem: (key: string, value: string) => {
      store.set(key, String(value));
    },
  } as Storage;
}

if (typeof localStorage === 'undefined' || typeof localStorage.setItem !== 'function') {
  Object.defineProperty(globalThis, 'localStorage', {
    value: createMemoryStorage(),
    configurable: true,
    writable: true,
  });
}
