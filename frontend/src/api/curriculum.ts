import { apiRequest } from './client';

export type PlanStatus = 'draft' | 'approved';
export type ProficiencyLevel = 'A1' | 'A2' | 'B1' | 'B2' | 'C1';

export interface Lesson {
  id: number;
  topic_id: number;
  code: string;
  title_en: string;
  title_ja: string;
  level: ProficiencyLevel;
  can_dos: string[];
  sort_order: number;
}

export interface Topic {
  id: number;
  code: string;
  title_en: string;
  title_ja: string;
  sort_order: number;
  lessons: Lesson[];
}

export interface LessonPlan {
  id: number;
  lesson_id: number;
  body_markdown: string;
  status: PlanStatus;
  version: number;
  updated_at: string;
  updated_by: number | null;
}

export interface LessonDetail {
  lesson: Lesson;
  plan: LessonPlan | null;
}

export function listTopics(): Promise<Topic[]> {
  return apiRequest<Topic[]>('/api/curriculum/topics');
}

export function getLessonDetail(lessonId: number): Promise<LessonDetail> {
  return apiRequest<LessonDetail>(`/api/curriculum/lessons/${lessonId}`);
}

export function savePlan(lessonId: number, bodyMarkdown: string): Promise<LessonPlan> {
  return apiRequest<LessonPlan>(`/api/curriculum/lessons/${lessonId}/plan`, {
    method: 'PUT',
    body: { body_markdown: bodyMarkdown },
  });
}

export function approvePlan(lessonId: number): Promise<LessonPlan> {
  return apiRequest<LessonPlan>(`/api/curriculum/lessons/${lessonId}/plan/approve`, {
    method: 'POST',
  });
}

export function revertPlan(lessonId: number): Promise<LessonPlan> {
  return apiRequest<LessonPlan>(`/api/curriculum/lessons/${lessonId}/plan/revert`, {
    method: 'POST',
  });
}
