import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { PROFILE_STORAGE_KEY } from './api/client';
import type { User } from './api/users';

// In-memory backend stand-in. Each test installs its own users array.
let users: User[] = [];
let nextId = 1;

function makeUser(overrides: Partial<User>): User {
  return {
    id: nextId++,
    name: 'Anon',
    name_ja: '',
    is_admin: false,
    level: 'A1',
    voice: 'Misa',
    llm_provider: 'claude',
    speech_provider: 'gcloud',
    correction_style: 'end_of_turn',
    explanation_language: 'en',
    show_hiragana: false,
    show_english: false,
    auto_stop_seconds: 7,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function fakeFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = typeof input === 'string' ? input : input.toString();
  const method = init?.method ?? 'GET';

  if (url === '/api/healthz') {
    return Promise.resolve(jsonResponse({ status: 'ok', service: 'japanese-study-backend' }));
  }
  if (url === '/api/profile' && method === 'GET') {
    return Promise.resolve(jsonResponse({ vocab: [], grammar: [], mistakes: [], topics: [] }));
  }
  if (url === '/api/users' && method === 'GET') {
    return Promise.resolve(jsonResponse([...users].sort((a, b) => a.name.localeCompare(b.name))));
  }
  if (url === '/api/users' && method === 'POST') {
    const body = init?.body ? (JSON.parse(init.body as string) as { name: string }) : { name: '' };
    if (users.some((u) => u.name === body.name)) {
      return Promise.resolve(jsonResponse({ detail: 'Name already taken' }, 409));
    }
    const u = makeUser({ name: body.name });
    users.push(u);
    return Promise.resolve(jsonResponse(u, 201));
  }
  const idMatch = url.match(/^\/api\/users\/(\d+)$/);
  if (idMatch) {
    const id = Number.parseInt(idMatch[1], 10);
    const idx = users.findIndex((u) => u.id === id);
    if (idx === -1) return Promise.resolve(jsonResponse({ detail: 'User not found' }, 404));
    if (method === 'GET') return Promise.resolve(jsonResponse(users[idx]));
    if (method === 'PATCH') {
      const patch = init?.body ? (JSON.parse(init.body as string) as Partial<User>) : {};
      users[idx] = { ...users[idx], ...patch };
      return Promise.resolve(jsonResponse(users[idx]));
    }
    if (method === 'DELETE') {
      users.splice(idx, 1);
      return Promise.resolve(jsonResponse({ ok: true }));
    }
  }
  return Promise.resolve(jsonResponse({ detail: `Unhandled ${method} ${url}` }, 500));
}

beforeEach(() => {
  users = [];
  nextId = 1;
  localStorage.clear();
  // BrowserRouter shares window.history across tests; reset to '/'.
  window.history.replaceState({}, '', '/');
  vi.stubGlobal('fetch', vi.fn(fakeFetch));
  // Confirm dialog returns true so delete tests proceed.
  vi.spyOn(window, 'confirm').mockReturnValue(true);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('App routing & profile flow', () => {
  it('shows the picker when no profile is selected', async () => {
    users.push(makeUser({ name: 'Mom', is_admin: true }));
    users.push(makeUser({ name: 'Sora' }));

    render(<App />);

    expect(await screen.findByRole('heading', { name: /who's practicing today/i })).toBeInTheDocument();
    const profileList = await screen.findByRole('list', { name: /profiles/i });
    const buttons = within(profileList).getAllByRole('button');
    expect(buttons).toHaveLength(2);
    expect(buttons[0]).toHaveTextContent('Mom');
    expect(buttons[1]).toHaveTextContent('Sora');
  });

  it('selecting a profile stores the id and renders the dashboard', async () => {
    const mom = makeUser({ name: 'Mom', is_admin: true });
    users.push(mom);

    render(<App />);

    const profileButton = await screen.findByRole('button', { name: /mom/i });
    fireEvent.click(profileButton);

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Mom');
    });
    expect(localStorage.getItem(PROFILE_STORAGE_KEY)).toBe(String(mom.id));
  });

  it('the picker can create a new profile and select it', async () => {
    render(<App />);

    expect(await screen.findByText(/no profiles yet/i)).toBeInTheDocument();
    const input = screen.getByLabelText(/add a profile/i);
    fireEvent.change(input, { target: { value: 'Kid1' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Kid1');
    });
  });

  it('switch profile clears storage and returns to the picker', async () => {
    users.push(makeUser({ name: 'Mom' }));
    localStorage.setItem(PROFILE_STORAGE_KEY, '1');

    render(<App />);

    await screen.findByRole('heading', { level: 1, name: /mom/i });
    fireEvent.click(screen.getByRole('button', { name: /switch profile/i }));

    expect(await screen.findByRole('heading', { name: /who's practicing today/i })).toBeInTheDocument();
    expect(localStorage.getItem(PROFILE_STORAGE_KEY)).toBeNull();
  });
});

describe('Settings page', () => {
  async function renderAsMom(): Promise<void> {
    const mom = makeUser({ name: 'Mom', is_admin: true });
    users.push(mom);
    users.push(makeUser({ name: 'Kid1' }));
    localStorage.setItem(PROFILE_STORAGE_KEY, String(mom.id));
    render(<App />);
    await screen.findByRole('heading', { level: 1, name: /mom/i });
    fireEvent.click(screen.getByRole('link', { name: /settings/i }));
    await screen.findByRole('heading', { name: /^profiles$/i });
  }

  it('lists all profiles and marks the current one', async () => {
    await renderAsMom();
    const list = await screen.findByRole('list', { name: /profiles/i });
    expect(within(list).getByText('Mom')).toBeInTheDocument();
    expect(within(list).getByText(/^you$/)).toBeInTheDocument();
    expect(within(list).getByText('Kid1')).toBeInTheDocument();
  });

  it('renames a profile', async () => {
    await renderAsMom();
    const list = await screen.findByRole('list', { name: /profiles/i });
    const kidRow = within(list).getByText('Kid1').closest('li')!;
    fireEvent.click(within(kidRow).getByRole('button', { name: /rename/i }));
    const editInput = within(kidRow).getByRole('textbox');
    fireEvent.change(editInput, { target: { value: 'Sora' } });
    fireEvent.click(within(kidRow).getByRole('button', { name: /save/i }));
    await waitFor(() => {
      expect(within(list).queryByText('Kid1')).not.toBeInTheDocument();
      expect(within(list).getByText('Sora')).toBeInTheDocument();
    });
  });

  it('deletes a non-current profile after confirming', async () => {
    await renderAsMom();
    const list = await screen.findByRole('list', { name: /profiles/i });
    const kidRow = within(list).getByText('Kid1').closest('li')!;
    fireEvent.click(within(kidRow).getByRole('button', { name: /delete kid1/i }));
    await waitFor(() => {
      expect(within(list).queryByText('Kid1')).not.toBeInTheDocument();
    });
  });

  it('disables the delete button for the current profile', async () => {
    await renderAsMom();
    const list = await screen.findByRole('list', { name: /profiles/i });
    const momRow = within(list).getByText('Mom').closest('li')!;
    expect(within(momRow).getByRole('button', { name: /delete mom/i })).toBeDisabled();
  });

  it('shows the server error when a name is already taken', async () => {
    await renderAsMom();
    const input = screen.getByLabelText(/add a profile/i);
    fireEvent.change(input, { target: { value: 'Kid1' } });
    fireEvent.click(screen.getByRole('button', { name: /^add$/i }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/already taken/i);
  });
});
