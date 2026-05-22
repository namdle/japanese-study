import { apiRequest } from './client';

export type ChatRole = 'user' | 'assistant';

export interface ChatMessage {
  role: ChatRole;
  content: string;
}

export interface ChatReply {
  reply: string;
  voice: string;
  provider: string;
}

export function sendChat(messages: ChatMessage[]): Promise<ChatReply> {
  return apiRequest<ChatReply>('/api/chat', {
    method: 'POST',
    body: { messages },
  });
}
