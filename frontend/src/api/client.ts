// Minimal fetch wrapper: prefixes /api requests, attaches X-User-Id from
// localStorage when present, parses JSON, and surfaces structured errors.

export const PROFILE_STORAGE_KEY = 'japanese-study.profileId';

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`HTTP ${status}: ${detail}`);
    this.name = 'ApiError';
  }
}

export function getStoredProfileId(): number | null {
  const raw = localStorage.getItem(PROFILE_STORAGE_KEY);
  if (!raw) return null;
  const id = Number.parseInt(raw, 10);
  return Number.isFinite(id) && id > 0 ? id : null;
}

export function setStoredProfileId(id: number): void {
  localStorage.setItem(PROFILE_STORAGE_KEY, String(id));
}

export function clearStoredProfileId(): void {
  localStorage.removeItem(PROFILE_STORAGE_KEY);
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  // Override or omit the X-User-Id header (e.g., the profile picker).
  withProfile?: boolean;
}

export async function apiRequest<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, withProfile = true } = opts;
  const headers: Record<string, string> = {};
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (withProfile) {
    const profileId = getStoredProfileId();
    if (profileId !== null) headers['X-User-Id'] = String(profileId);
  }

  const res = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const errBody = (await res.json()) as { detail?: unknown };
      if (typeof errBody.detail === 'string') detail = errBody.detail;
      else if (errBody.detail) detail = JSON.stringify(errBody.detail);
    } catch {
      // ignore JSON parse errors; keep statusText
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}
