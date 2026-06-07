// SSE chat client.
//
// Backend ships these named SSE events:
//   - meta   : JSON {message_id, conversation_id, intent, intent_source}
//   - delta  : raw text chunk (concatenate)
//   - final  : replacement text after contract guard (if changed)
//   - error  : error message; stream is over right after
//   - done   : empty terminator
//
// We deliberately use POST + ReadableStream (not EventSource) because:
//   1. EventSource is GET-only and the URL would balloon when we
//      forward conversation history.
//   2. We need Authorization headers on the streaming connection.

import { apiBase } from './client';
import { useAuthStore } from '@/stores/auth';

export type SseEventKind = 'meta' | 'delta' | 'final' | 'error' | 'done';

export interface SseEvent {
  kind: SseEventKind;
  data: string;
}

export interface ChatStreamArgs {
  conversation_id: string;
  message: string;
  personality?: 'introvert' | 'balanced' | 'extrovert';
  signal?: AbortSignal;
}

export async function* streamChat(
  args: ChatStreamArgs,
): AsyncGenerator<SseEvent, void, void> {
  const auth = useAuthStore();
  const url = `${apiBase()}/api/chat`;
  const resp = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(auth.token ? { Authorization: `Bearer ${auth.token}` } : {}),
    },
    body: JSON.stringify({
      conversation_id: args.conversation_id,
      message: args.message,
      personality: args.personality,
    }),
    signal: args.signal,
  });

  if (!resp.ok || !resp.body) {
    let errText = `chat ${resp.status}`;
    try {
      errText = await resp.text();
    } catch {
      // best effort
    }
    yield { kind: 'error', data: errText };
    return;
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';

  // Each SSE frame is "event: <kind>\ndata: <text>\n\n" — parse incrementally.
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx = buf.indexOf('\n\n');
    while (idx !== -1) {
      const frame = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const evt = parseFrame(frame);
      if (evt) yield evt;
      idx = buf.indexOf('\n\n');
    }
  }
  // flush any trailing frame (rare, but possible if upstream forgets the
  // final blank line)
  if (buf.trim()) {
    const evt = parseFrame(buf);
    if (evt) yield evt;
  }
}

function parseFrame(frame: string): SseEvent | null {
  let kind: SseEventKind | null = null;
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith(':')) continue; // SSE comment / heartbeat
    if (line.startsWith('event:')) {
      kind = line.slice(6).trim() as SseEventKind;
    } else if (line.startsWith('data:')) {
      // Strip the single space after "data:" if present, per SSE spec.
      const v = line.startsWith('data: ') ? line.slice(6) : line.slice(5);
      dataLines.push(v);
    }
  }
  if (!kind) return null;
  return { kind, data: dataLines.join('\n') };
}
