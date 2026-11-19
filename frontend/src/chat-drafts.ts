import type { PromptDraft } from '@particle-academy/react-fancy';

export const emptyDraft = (): PromptDraft => ({ v: 1, text: '', pills: [], attachments: [] });

/** Account-owned UI drafts and retry identities, never provider credentials. */
export class ChatDrafts {
  private entries = new Map<string, PromptDraft>();
  private requests = new Map<string, { text: string; id: string }>();
  read(account: string): PromptDraft { return structuredClone(this.entries.get(account) ?? emptyDraft()); }
  write(account: string, value: PromptDraft) { this.entries.set(account, structuredClone(value)); }
  request(account: string, text: string, makeId = () => crypto.randomUUID()): string {
    const old = this.requests.get(account);
    if (old?.text === text) return old.id;
    const id = makeId(); this.requests.set(account, { text, id }); return id;
  }
  acknowledge(account: string, id: string) {
    if (this.requests.get(account)?.id === id) this.requests.delete(account);
  }
}

/** True for an Excel workbook name. */
export const isWorkbook = (name: string) => /\.xlsx$/i.test(name);
/** True for a file sent as its exact bytes (a workbook or a PDF), not as text. */
export const sentAsBytes = (name: string) => /\.(xlsx|pdf)$/i.test(name);
/** The largest file of this kind Maipa accepts, in bytes. */
export const attachmentLimit = (name: string) => /\.pdf$/i.test(name) ? 716_800 : 524_288;
/** A CSV, Excel or PDF statement the person picked in this session: the attachments Maipa can receive today. */
export function sendableAttachment(value: unknown): value is { name: string; bytes: number; file: { text(): Promise<string>; arrayBuffer(): Promise<ArrayBuffer> } } {
  const item = value as { name?: unknown; bytes?: unknown; file?: { text?: unknown; arrayBuffer?: unknown } } | null;
  return !!item && typeof item.name === 'string' && /^[^/\\:*?"<>|]{1,123}\.(csv|xlsx|pdf)$/i.test(item.name) && typeof item.bytes === 'number'
    && item.bytes > 0 && item.bytes <= attachmentLimit(item.name) && !!item.file
    && typeof (sentAsBytes(item.name) ? item.file.arrayBuffer : item.file.text) === 'function';
}
/** The line added to the message so the agent can find each uploaded file. */
export function attachmentNote(saved: { id: string; name: string; size: number; lines: number }[]): string {
  return saved.map(file => `[Attached file: ${file.name} — attachment_id ${file.id}, ${file.size} bytes, ${file.lines} lines]`).join('\n');
}
export function sendProblem(text: string, attachments: readonly unknown[]): string | null {
  if (attachments.length > 3 || !attachments.every(sendableAttachment))
    return 'Only CSV or Excel (.xlsx) files up to 512 KB and PDF files up to 700 KB (at most 3) can be sent to Maipa. Remove them to send this message.';
  if (!text.trim()) return 'Write a message before sending.';
  if (text.length > 8000) return 'Keep the message within 8,000 characters. Your draft has been kept.';
  return null;
}
