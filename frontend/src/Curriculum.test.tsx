import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { PROFILE_STORAGE_KEY } from './api/client';
import type { User } from './api/users';
import type { LessonDetail, LessonPlan, Topic } from './api/curriculum';

let users: User[] = [];
let topicsResponse: Topic[] = [];
let lessonDetail: LessonDetail | null = null;
let savedPlan: LessonPlan | null = null;

function makeUser(overrides: Partial<User>): User {
  return {
    id: 1,
    name: 'Mom',
    name_ja: '',
    is_admin: true,
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
    return Promise.resolve(jsonResponse({ status: 'ok', service: 'x' }));
  }
  const userMatch = url.match(/^\/api\/users\/(\d+)$/);
  if (userMatch && method === 'GET') {
    const id = Number.parseInt(userMatch[1], 10);
    const u = users.find((x) => x.id === id);
    return Promise.resolve(u ? jsonResponse(u) : jsonResponse({ detail: 'nf' }, 404));
  }
  if (url === '/api/curriculum/topics' && method === 'GET') {
    return Promise.resolve(jsonResponse(topicsResponse));
  }
  const lessonMatch = url.match(/^\/api\/curriculum\/lessons\/(\d+)$/);
  if (lessonMatch && method === 'GET') {
    return Promise.resolve(jsonResponse(lessonDetail));
  }
  const planSaveMatch = url.match(/^\/api\/curriculum\/lessons\/(\d+)\/plan$/);
  if (planSaveMatch && method === 'PUT') {
    const lessonId = Number.parseInt(planSaveMatch[1], 10);
    const body = init?.body
      ? (JSON.parse(init.body as string) as { body_markdown: string })
      : { body_markdown: '' };
    savedPlan = {
      id: 1,
      lesson_id: lessonId,
      body_markdown: body.body_markdown,
      status: 'draft',
      version: (savedPlan?.version ?? 0) + 1,
      updated_at: new Date().toISOString(),
      updated_by: 1,
    };
    if (lessonDetail) lessonDetail = { ...lessonDetail, plan: savedPlan };
    return Promise.resolve(jsonResponse(savedPlan));
  }
  return Promise.resolve(jsonResponse({ detail: 'unhandled' }, 500));
}

beforeEach(() => {
  users = [makeUser({})];
  topicsResponse = [
    {
      id: 1,
      code: 'T01_GREETINGS',
      title_en: 'Greetings & Self-Introduction',
      title_ja: 'あいさつ',
      sort_order: 1,
      lessons: [
        {
          id: 11,
          topic_id: 1,
          code: 'T01_A1',
          title_en: 'Saying hi',
          title_ja: 'こんにちは',
          level: 'A1',
          can_dos: ['Greet someone'],
          sort_order: 1,
        },
      ],
    },
  ];
  lessonDetail = {
    lesson: topicsResponse[0].lessons[0],
    plan: null,
  };
  savedPlan = null;
  localStorage.setItem(PROFILE_STORAGE_KEY, '1');
  window.history.replaceState({}, '', '/curriculum');
  vi.stubGlobal('fetch', vi.fn(fakeFetch));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('Curriculum page', () => {
  it('lists topics and lessons in the tree', async () => {
    render(<App />);
    expect(await screen.findByRole('heading', { level: 1, name: /^curriculum$/i })).toBeInTheDocument();
    expect(await screen.findByText(/greetings/i)).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /A1 Saying hi/i })).toBeInTheDocument();
  });

  it('shows the lesson editor for admins and lets them save a draft', async () => {
    render(<App />);
    await screen.findByRole('heading', { level: 1, name: /^curriculum$/i });

    // Editor textarea is visible to admins.
    const textarea = await screen.findByLabelText(/markdown/i);
    fireEvent.change(textarea, { target: { value: '# Greet warmly' } });

    fireEvent.click(screen.getByRole('button', { name: /save draft/i }));

    await waitFor(() => {
      expect(screen.getByText(/saved as draft/i)).toBeInTheDocument();
    });
    expect(savedPlan?.body_markdown).toBe('# Greet warmly');
  });

  it('hides the editor and shows read-only view for non-admins', async () => {
    users = [makeUser({ is_admin: false })];
    lessonDetail = {
      lesson: topicsResponse[0].lessons[0],
      plan: {
        id: 5,
        lesson_id: 11,
        body_markdown: '# Approved plan body',
        status: 'approved',
        version: 1,
        updated_at: new Date().toISOString(),
        updated_by: 2,
      },
    };

    render(<App />);
    await screen.findByRole('heading', { level: 1, name: /^curriculum$/i });

    expect(screen.queryByLabelText(/markdown/i)).not.toBeInTheDocument();
    expect(await screen.findByText(/approved plan body/i)).toBeInTheDocument();
  });
});
