import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { PROFILE_STORAGE_KEY } from './api/client';
import type { User } from './api/users';
import type { ActiveSession, LessonInfo, SessionDetail, Turn } from './api/sessions';

let users: User[] = [];
let activeResponse: ActiveSession = { active: null, next_lesson: null };
let nextTurnReply = 'はい!';
let startedSessions = 0;
let endedSessions = 0;
const startCalls: { mode?: string }[] = [];
let endSummary: string | null = null;

function makeUser(overrides: Partial<User>): User {
  return {
    id: 1,
    name: 'Sora',
    is_admin: false,
    level: 'A1',
    voice: 'Misa',
    llm_provider: 'claude',
    speech_provider: 'gcloud',
    correction_style: 'end_of_turn',
    explanation_language: 'en',
    show_hiragana: false,
    show_english: false,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function makeLesson(overrides: Partial<LessonInfo> = {}): LessonInfo {
  return {
    id: 11,
    title_en: 'Saying hi',
    title_ja: 'こんにちは',
    level: 'A1',
    can_dos: ['Greet someone'],
    topic_title_en: 'Greetings',
    topic_title_ja: 'あいさつ',
    ...overrides,
  };
}

function makeTurn(overrides: Partial<Turn> & Pick<Turn, 'role' | 'text'>): Turn {
  return {
    id: Math.floor(Math.random() * 1_000_000),
    audio_url: null,
    created_at: new Date().toISOString(),
    ...overrides,
  };
}

function makeSession(overrides: Partial<SessionDetail> = {}): SessionDetail {
  return {
    session: {
      id: 100,
      user_id: 1,
      lesson_id: 11,
      lesson_plan_id: 5,
      mode: 'freeform',
      tutor_voice: 'Misa',
      llm_provider: 'claude',
      speech_provider: 'gcloud',
      started_at: new Date().toISOString(),
      ended_at: null,
      summary: null,
    },
    lesson: makeLesson(),
    turns: [makeTurn({ role: 'assistant', text: 'こんにちは!' })],
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
    return Promise.resolve(jsonResponse({ status: 'ok', service: 'x' }));
  }
  const userMatch = url.match(/^\/api\/users\/(\d+)$/);
  if (userMatch && method === 'GET') {
    const id = Number.parseInt(userMatch[1], 10);
    const u = users.find((x) => x.id === id);
    return Promise.resolve(u ? jsonResponse(u) : jsonResponse({ detail: 'nf' }, 404));
  }
  if (url === '/api/sessions/active' && method === 'GET') {
    return Promise.resolve(jsonResponse(activeResponse));
  }
  if (url === '/api/sessions/start' && method === 'POST') {
    startedSessions++;
    const body = init?.body
      ? (JSON.parse(init.body as string) as { mode?: string })
      : {};
    startCalls.push({ mode: body.mode });
    const started = makeSession({
      session: {
        ...makeSession().session,
        mode: (body.mode as 'freeform' | 'three_phase') ?? 'freeform',
      },
    });
    activeResponse = { active: started, next_lesson: null };
    return Promise.resolve(jsonResponse(started, 201));
  }
  if (url === '/api/sessions/start-from-image' && method === 'POST') {
    startedSessions++;
    const started = makeSession({
      session: {
        ...makeSession().session,
        lesson_id: null,
        seed_image_url: '/api/uploads/1/abc.jpg',
      },
      lesson: null,
      turns: [makeTurn({ role: 'assistant', text: '画像を見ました。' })],
    });
    activeResponse = { active: started, next_lesson: null };
    return Promise.resolve(jsonResponse(started, 201));
  }
  const turnMatch = url.match(/^\/api\/sessions\/(\d+)\/turn$/);
  if (turnMatch && method === 'POST') {
    const body = init?.body
      ? (JSON.parse(init.body as string) as { content: string })
      : { content: '' };
    if (!activeResponse.active) {
      return Promise.resolve(jsonResponse({ detail: 'no session' }, 400));
    }
    const updated: SessionDetail = {
      ...activeResponse.active,
      turns: [
        ...activeResponse.active.turns,
        makeTurn({ role: 'user', text: body.content }),
        makeTurn({ role: 'assistant', text: nextTurnReply }),
      ],
    };
    activeResponse = { ...activeResponse, active: updated };
    return Promise.resolve(jsonResponse(updated));
  }
  const endMatch = url.match(/^\/api\/sessions\/(\d+)\/end$/);
  if (endMatch && method === 'POST') {
    endedSessions++;
    if (activeResponse.active) {
      activeResponse = {
        active: null,
        next_lesson: activeResponse.next_lesson,
      };
    }
    return Promise.resolve(
      jsonResponse({
        ...makeSession().session,
        ended_at: new Date().toISOString(),
        summary: endSummary,
      }),
    );
  }
  return Promise.resolve(jsonResponse({ detail: `unhandled ${method} ${url}` }, 500));
}

beforeEach(() => {
  users = [makeUser({})];
  activeResponse = { active: null, next_lesson: null };
  nextTurnReply = 'はい!';
  startedSessions = 0;
  endedSessions = 0;
  startCalls.length = 0;
  endSummary = null;
  localStorage.setItem(PROFILE_STORAGE_KEY, '1');
  window.history.replaceState({}, '', '/chat');
  vi.stubGlobal('fetch', vi.fn(fakeFetch));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Practice page (session-aware)', () => {
  it('shows the no-plans message when no lessons are approved', async () => {
    render(<App />);
    expect(
      await screen.findByRole('heading', { level: 1, name: /practice with misa/i }),
    ).toBeInTheDocument();
    expect(await screen.findByText(/no approved lesson plans yet/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /start session/i })).not.toBeInTheDocument();
  });

  it('shows the next lesson preview and starts a session on click', async () => {
    activeResponse = { active: null, next_lesson: makeLesson() };
    render(<App />);

    expect(await screen.findByText(/next up: saying hi/i)).toBeInTheDocument();
    expect(screen.getByText(/level a1/i)).toBeInTheDocument();
    expect(screen.getByText(/greet someone/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /start session/i }));

    // Opening greeting from the (mocked) backend.
    await waitFor(() => {
      expect(screen.getByText('こんにちは!')).toBeInTheDocument();
    });
    expect(startedSessions).toBe(1);
  });

  it('resumes the active session on page load', async () => {
    const existing = makeSession({
      turns: [
        makeTurn({ id: 1, role: 'assistant', text: 'やあ!' }),
        makeTurn({ id: 2, role: 'user', text: 'やあ' }),
        makeTurn({ id: 3, role: 'assistant', text: '元気?' }),
      ],
    });
    activeResponse = { active: existing, next_lesson: null };
    render(<App />);

    expect(
      await screen.findByRole('heading', { level: 1, name: /practice with misa/i }),
    ).toBeInTheDocument();
    expect(screen.getByText('やあ!')).toBeInTheDocument();
    expect(screen.getByText('やあ')).toBeInTheDocument();
    expect(screen.getByText('元気?')).toBeInTheDocument();
  });

  it('sends a text turn and appends the assistant reply', async () => {
    const existing = makeSession();
    activeResponse = { active: existing, next_lesson: null };
    nextTurnReply = 'いいですね';
    render(<App />);

    await screen.findByText('こんにちは!');
    const input = screen.getByLabelText(/your message/i);
    fireEvent.change(input, { target: { value: 'やあ' } });
    fireEvent.click(screen.getByRole('button', { name: /^send$/i }));

    await waitFor(() => {
      expect(screen.getByText('やあ')).toBeInTheDocument();
    });
    expect(await screen.findByText('いいですね')).toBeInTheDocument();
  });

  it('end session returns to the lesson preview', async () => {
    const existing = makeSession();
    activeResponse = { active: existing, next_lesson: makeLesson() };
    render(<App />);

    await screen.findByText('こんにちは!');
    fireEvent.click(screen.getByRole('button', { name: /end session/i }));

    expect(await screen.findByText(/next up: saying hi/i)).toBeInTheDocument();
    expect(endedSessions).toBe(1);
  });

  it('shows the mic button (jsdom flags MediaRecorder as unsupported)', async () => {
    activeResponse = { active: makeSession(), next_lesson: null };
    render(<App />);
    await screen.findByText('こんにちは!');
    const micButton = screen.getByRole('button', { name: /mic unsupported|start recording/i });
    expect(micButton).toBeDisabled();
  });

  it('defaults the mode toggle to freeform and sends it on Start', async () => {
    activeResponse = { active: null, next_lesson: makeLesson() };
    render(<App />);
    await screen.findByText(/next up/i);

    expect(screen.getByLabelText(/free-form chat/i)).toBeChecked();
    expect(screen.getByLabelText(/3-phase lesson/i)).not.toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: /start session/i }));
    await waitFor(() => expect(startCalls.length).toBe(1));
    expect(startCalls[0].mode).toBe('freeform');
  });

  it('sends three_phase mode when the user picks the 3-phase option', async () => {
    activeResponse = { active: null, next_lesson: makeLesson() };
    render(<App />);
    await screen.findByText(/next up/i);

    fireEvent.click(screen.getByLabelText(/3-phase lesson/i));
    fireEvent.click(screen.getByRole('button', { name: /start session/i }));
    await waitFor(() => expect(startCalls.length).toBe(1));
    expect(startCalls[0].mode).toBe('three_phase');
  });

  it('shows the wrap-up summary panel after End session', async () => {
    activeResponse = { active: makeSession(), next_lesson: makeLesson() };
    endSummary = 'Great work! A couple of things to remember:\n- Use です more.';
    render(<App />);

    await screen.findByText('こんにちは!');
    fireEvent.click(screen.getByRole('button', { name: /end session/i }));

    expect(await screen.findByRole('heading', { name: /session wrap-up/i })).toBeInTheDocument();
    expect(screen.getByText(/use です more/i)).toBeInTheDocument();
  });

  it('renders hiragana and english aids under tutor turns when present', async () => {
    activeResponse = {
      active: makeSession({
        turns: [
          makeTurn({
            role: 'assistant',
            text: '元気ですか?',
            hiragana: 'げんきですか?',
            english: 'How are you?',
          }),
        ],
      }),
      next_lesson: null,
    };
    render(<App />);
    expect(await screen.findByText('元気ですか?')).toBeInTheDocument();
    expect(screen.getByText('げんきですか?')).toBeInTheDocument();
    expect(screen.getByText('How are you?')).toBeInTheDocument();
  });

  it('shows the image upload panel on the lesson preview screen', async () => {
    activeResponse = { active: null, next_lesson: makeLesson() };
    render(<App />);
    expect(
      await screen.findByRole('region', { name: /upload textbook image/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /choose image/i })).toBeInTheDocument();
  });

  it('uploading an image starts a session and shows the seed image', async () => {
    activeResponse = { active: null, next_lesson: makeLesson() };
    render(<App />);

    await screen.findByRole('button', { name: /choose image/i });
    const fileInput = screen.getByTestId('image-upload-input') as HTMLInputElement;
    const file = new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])], 'page.jpg', {
      type: 'image/jpeg',
    });
    fireEvent.change(fileInput, { target: { files: [file] } });

    expect(await screen.findByText('画像を見ました。')).toBeInTheDocument();
    const seedImg = screen.getByAltText(/uploaded textbook page/i) as HTMLImageElement;
    expect(seedImg.getAttribute('src')).toContain('/api/uploads/');
  });
});
