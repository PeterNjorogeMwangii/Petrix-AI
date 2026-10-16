import { DatePipe } from '@angular/common';
import { Component, ElementRef, EventEmitter, Input, OnChanges, OnDestroy, Output, ViewChild, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Agent } from '../../../core/models';
import { errorMessage } from '../../../core/api.service';
import { IconComponent } from '../../../shared/icon.component';
import { MessageTextComponent } from '../../../shared/message-text.component';
import { WorkflowEvent, WorkflowEventType, WorkflowMessage, WorkflowTestResult } from '../workflow.models';
import { WorkflowsApi } from '../workflows.api';

@Component({
  selector: 'petrix-workflow-tester', imports: [FormsModule, DatePipe, IconComponent, MessageTextComponent],
  templateUrl: './workflow-tester.component.html', styleUrl: './workflow-tester.component.css'
})
export class WorkflowTesterComponent implements OnChanges, OnDestroy {
  @Input() agent: Agent | null = null;
  @Input() canManage = false;
  @Input() languages: { code: string; name: string }[] = [];
  @Input() savedRevision = 0;
  @Input() resetKey = 0;
  @Output() busyChange = new EventEmitter<boolean>();
  @ViewChild('messageList') messageList?: ElementRef<HTMLElement>;
  private readonly api = inject(WorkflowsApi);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly messages = signal<WorkflowMessage[]>([]);
  readonly result = signal<WorkflowTestResult | null>(null);
  text = ''; language = 'en';
  eventType: WorkflowEventType = 'attachment_received';
  attachmentType = 'image'; attachmentName = 'test-document.png'; rating = 5;
  private requestId = 0;
  private inputKey = '';

  ngOnChanges() {
    const key = `${this.agent?.id}:${this.agent?.name}:${this.agent?.default_language}:${this.agent?.languages.join(',')}:${this.canManage}:${this.resetKey}`;
    if (key !== this.inputKey) {
      this.inputKey = key; this.language = this.agent?.default_language || 'en'; void this.restart();
    }
  }
  ngOnDestroy() { ++this.requestId; }
  languageName(code: string) { return this.languages.find(value => value.code === code)?.name || code; }
  ready() { return !!this.agent && this.canManage && !!this.result() && !this.result()?.closed; }
  private setBusy(value: boolean) { this.busy.set(value); this.busyChange.emit(value); }
  async restart() {
    const request = ++this.requestId;
    this.messages.set([]); this.result.set(null); this.error.set(''); this.text = '';
    if (!this.agent || !this.canManage) { this.setBusy(false); return; }
    const id = this.agent.id; this.setBusy(true);
    try {
      const result = await this.api.event(id, { event: 'chat_started', language: this.language });
      if (request === this.requestId) this.accept(result);
    } catch (error) { if (request === this.requestId) this.error.set(errorMessage(error)); }
    finally { if (request === this.requestId) this.setBusy(false); }
  }
  private accept(result: WorkflowTestResult) {
    this.result.set(result); this.language = result.language; this.messages.update(values => [...values, ...result.messages]); this.scroll();
  }
  variableEntries() { return Object.entries(this.result()?.variables || {}).sort(([left], [right]) => left.localeCompare(right)); }
  variableText(value: unknown) { return typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? 'null'; }
  matchingLabel(method: string) {
    return ({ exact: 'Exact phrase', fuzzy: 'Fuzzy text', semantic: 'Semantic embeddings', llm: 'Groq intent',
      clarification: 'Clarification needed', fallback: 'Fallback', none: 'No match', event: 'Event' } as Record<string, string>)[method] || method;
  }
  matchingScore(score: number | null) { return score === null ? '—' : `${Math.round(score * 100)}%`; }
  matchingStatus(status: string) { return status.replaceAll('_', ' '); }
  entityEntries() { return Object.entries(this.result()?.matching?.entities || {}); }
  private scroll() { setTimeout(() => { const element = this.messageList?.nativeElement; if (element) element.scrollTop = element.scrollHeight; }, 0); }
  onEnter(event: Event) { if (event instanceof KeyboardEvent && !event.shiftKey && !event.isComposing) { event.preventDefault(); void this.send(); } }
  async send(quickReply?: string) {
    if (quickReply) this.text = quickReply;
    const content = this.text.trim(); if (!content || !this.ready() || this.busy()) return;
    this.text = '';
    const pendingId = crypto.randomUUID();
    this.messages.update(values => [...values, { id: pendingId, role: 'user', content, created_at: new Date().toISOString() }]);
    const succeeded = await this.dispatch({ event: 'message_received', content });
    if (!succeeded) { this.messages.update(values => values.filter(value => value.id !== pendingId)); this.text = content; }
  }
  canSendEvent() {
    if (!this.agent || !this.canManage || !this.result() || this.busy()) return false;
    return this.eventType === 'collect_feedback' ? this.result()!.closed && !this.result()!.feedback_submitted : !this.result()!.closed;
  }
  async simulate() {
    if (!this.canSendEvent()) return;
    const event: WorkflowEvent = { event: this.eventType };
    if (this.eventType === 'attachment_received') {
      if (!this.attachmentName.trim()) { this.error.set('Enter an attachment name.'); return; }
      event.attachment = { name: this.attachmentName, media_type: this.attachmentType };
    }
    if (this.eventType === 'collect_feedback') event.rating = Number(this.rating);
    await this.dispatch(event);
  }
  private async dispatch(event: WorkflowEvent) {
    if (!this.agent || !this.result() || this.busy()) return false;
    const request = ++this.requestId; const id = this.agent.id; this.setBusy(true); this.error.set(''); this.scroll();
    try {
      const result = await this.api.event(id, { ...event, conversation_id: this.result()!.conversation_id, language: this.language });
      if (request !== this.requestId) return false;
      if (event.event !== 'message_received') {
        const names: Record<string, string> = { attachment_received: `Attachment test: ${this.attachmentName}`, chat_closed: 'Chat closed', collect_feedback: `CSAT submitted: ${this.rating} / 5`, chat_timeout: 'Chat timeout', ai_timeout: 'AI timeout' };
        this.messages.update(values => [...values, { id: crypto.randomUUID(), role: 'event', content: names[event.event] || event.event, created_at: new Date().toISOString() }]);
      }
      this.accept(result); return true;
    } catch (error) { if (request === this.requestId) this.error.set(errorMessage(error)); return false; }
    finally { if (request === this.requestId) this.setBusy(false); }
  }
}
