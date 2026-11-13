import { DatePipe } from '@angular/common';
import { Component, ElementRef, EventEmitter, Input, OnChanges, OnDestroy, Output, ViewChild, inject, signal } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { FormsModule } from '@angular/forms';
import { Agent } from '../../../core/models';
import { errorMessage } from '../../../core/api.service';
import { IconComponent } from '../../../shared/icon.component';
import { MessageTextComponent } from '../../../shared/message-text.component';
import { CarouselButton, FeedbackChannel, WorkflowEvent, WorkflowEventType, WorkflowMessage, WorkflowTestResult } from '../workflow.models';
import { FeedbackFormComponent, FeedbackSubmission } from './feedback-form.component';
import { AttachmentPreviewComponent } from '../attachment-preview.component';
import { CarouselMessageComponent } from './carousel-message.component';
import { validCarouselLink } from '../actions/show-carousel/carousel-config';
import { WorkflowsApi } from '../workflows.api';

@Component({
  selector: 'petrix-workflow-tester', imports: [FormsModule, DatePipe, IconComponent, MessageTextComponent, AttachmentPreviewComponent, CarouselMessageComponent, FeedbackFormComponent],
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
  @ViewChild('surveyUpload') surveyUpload?: ElementRef<HTMLInputElement>;
  private readonly api = inject(WorkflowsApi);
  private readonly sanitizer = inject(DomSanitizer);
  readonly webview = signal<{url: string; title: string; src: SafeResourceUrl} | null>(null);
  private webviewOpener: HTMLElement | null = null;
  @ViewChild('webviewClose') webviewClose?: ElementRef<HTMLButtonElement>;
  readonly busy = signal(false);
  readonly error = signal('');
  readonly messages = signal<WorkflowMessage[]>([]);
  readonly result = signal<WorkflowTestResult | null>(null);
  text = ''; language = 'en';
  channel: FeedbackChannel = 'webchat';
  selectedFiles: File[] = [];
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
  ngOnDestroy() { ++this.requestId; this.webview.set(null); }
  languageName(code: string) { return this.languages.find(value => value.code === code)?.name || code; }
  ready() { return !!this.agent && this.canManage && !!this.result() && !this.result()?.closed; }
  private setBusy(value: boolean) { this.busy.set(value); this.busyChange.emit(value); }
  async restart() {
    this.closeWebview(false);
    const request = ++this.requestId;
    this.messages.set([]); this.result.set(null); this.error.set(''); this.text = ''; this.selectedFiles = [];
    if (!this.agent || !this.canManage) { this.setBusy(false); return; }
    const id = this.agent.id; this.setBusy(true);
    try {
      const result = await this.api.event(id, { event: 'chat_started', language: this.language, channel: this.channel });
      if (request === this.requestId) this.accept(result);
    } catch (error) { if (request === this.requestId) this.error.set(errorMessage(error)); }
    finally { if (request === this.requestId) this.setBusy(false); }
  }
  private accept(result: WorkflowTestResult) {
    if (result.closed) this.closeWebview(false);
    if (this.result()?.survey?.answer_token !== result.survey?.answer_token) {
      this.selectedFiles = []; if (this.surveyUpload) this.surveyUpload.nativeElement.value = '';
    }
    this.result.set(result); this.language = result.language; this.channel = result.channel || 'webchat'; this.messages.update(values => [...values, ...result.messages]); this.scroll();
  }
  carouselActive(message: WorkflowMessage) {
    if (!this.ready() || this.busy() || this.result()?.survey) return false;
    const messages = this.messages(); const index = messages.findIndex(value => value.id === message.id);
    return index >= 0 && !messages.slice(index + 1).some(value => value.role === 'user' || value.role === 'event' || value.survey || value.quick_replies?.length || value.carousel?.cards.some(card => card.buttons.some(button => button.type === 'reply')));
  }
  openWebview(value: {button: CarouselButton; opener: EventTarget | null}) {
    if (!validCarouselLink(value.button.url)) { this.error.set('This webpage URL is not valid.'); return; }
    this.webviewOpener = value.opener instanceof HTMLElement ? value.opener : null;
    // The URL is validated locally and by the backend. The static iframe
    // sandbox retains an opaque origin, even for a same-origin link.
    this.webview.set({ url: value.button.url, title: value.button.label, src: this.sanitizer.bypassSecurityTrustResourceUrl(value.button.url) });
    setTimeout(() => this.webviewClose?.nativeElement.focus(), 0);
  }
  closeWebview(restoreFocus = true) { this.webview.set(null); if (restoreFocus) this.webviewOpener?.focus(); this.webviewOpener = null; }
  surveyUsesFiles() { return ['attachment', 'attachments'].includes(this.result()?.survey?.answer_type || ''); }
  surveyAnswerLabel(type: string) { return ({text:'Text',number:'Number',email:'Email',phone:'Phone',attachment:'One file',attachments:'Files'} as Record<string,string>)[type] || type; }
  fileSize(size: number | null) { return size === null ? 'Hosted file' : size < 1024 ? `${size} B` : size < 1048576 ? `${Math.round(size / 1024)} KiB` : `${(size / 1048576).toFixed(1)} MiB`; }
  selectFiles(event: Event) {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files || []);
    this.error.set('');
    const maximum = this.result()?.survey?.answer_type === 'attachment' ? 1 : 10;
    if (files.length > maximum || files.some(file => !file.size || file.size > 10485760) || files.reduce((total, file) => total + file.size, 0) > 20971520) {
      this.error.set(`Choose up to ${maximum} non-empty file${maximum === 1 ? '' : 's'}, at most 10 MiB each and 20 MiB total.`);
      this.selectedFiles = []; input.value = ''; return;
    }
    this.selectedFiles = files;
  }
  removeFile(index: number) {
    this.selectedFiles = this.selectedFiles.filter((_, position) => position !== index);
    if (this.surveyUpload) this.surveyUpload.nativeElement.value = '';
  }
  async uploadAnswer() {
    const current = this.result(); const progress = current?.survey;
    if (!this.agent || !current || !progress || !this.ready() || this.busy() || !this.selectedFiles.length || !this.surveyUsesFiles()) return;
    const files = [...this.selectedFiles]; const request = ++this.requestId;
    this.setBusy(true); this.error.set(''); this.scroll();
    try {
      const result = await this.api.uploadSurveyAnswer(this.agent.id, current.conversation_id, progress.answer_token, files, this.language);
      if (request !== this.requestId) return;
      this.messages.update(values => [...values, { id: crypto.randomUUID(), role: 'user',
        content: `Uploaded: ${files.map(file => file.name).join(', ')}`, created_at: new Date().toISOString(), attachments: result.attachments || [] }]);
      this.accept(result);
    } catch (error) { if (request === this.requestId) this.error.set(errorMessage(error)); }
    finally { if (request === this.requestId) this.setBusy(false); }
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
  async submitFeedback(submission: FeedbackSubmission) {
    const current = this.result();
    if (!this.canManage || !current?.feedback || !current.closed || current.feedback_submitted || this.busy()) return;
    await this.dispatch({ event: 'collect_feedback', rating: submission.rating, feedback_answers: submission.answers, feedback_token: current.feedback.token });
  }
  canSendEvent() {
    if (!this.agent || !this.canManage || !this.result() || this.busy()) return false;
    return this.eventType === 'collect_feedback' ? this.result()!.closed && !!this.result()!.feedback && !this.result()!.feedback?.fields.length && !!this.result()!.feedback?.rating_enabled && !this.result()!.feedback_submitted : !this.result()!.closed;
  }
  async simulate() {
    if (!this.canSendEvent()) return;
    const event: WorkflowEvent = { event: this.eventType };
    if (this.eventType === 'attachment_received') {
      if (!this.attachmentName.trim()) { this.error.set('Enter an attachment name.'); return; }
      event.attachment = { name: this.attachmentName, media_type: this.attachmentType };
    }
    if (this.eventType === 'collect_feedback') { event.rating = Number(this.rating); event.feedback_token = this.result()?.feedback?.token; }
    await this.dispatch(event);
  }
  private async dispatch(event: WorkflowEvent) {
    if (!this.agent || !this.result() || this.busy()) return false;
    const request = ++this.requestId; const id = this.agent.id; this.setBusy(true); this.error.set(''); this.scroll();
    try {
      const token = event.event === 'message_received' || event.event === 'attachment_received' ? this.result()?.survey?.answer_token : undefined;
      const result = await this.api.event(id, { ...event, conversation_id: this.result()!.conversation_id, language: this.language, channel: this.channel, survey_answer_token: token });
      if (request !== this.requestId) return false;
      if (event.event !== 'message_received') {
        const names: Record<string, string> = { attachment_received: `Attachment test: ${this.attachmentName}`, chat_closed: 'Chat closed', collect_feedback: event.rating ? `Feedback submitted: ${event.rating} / 5` : 'Feedback submitted', chat_timeout: 'Chat timeout', ai_timeout: 'AI timeout' };
        this.messages.update(values => [...values, { id: crypto.randomUUID(), role: 'event', content: names[event.event] || event.event, created_at: new Date().toISOString() }]);
      }
      this.accept(result); return true;
    } catch (error) { if (request === this.requestId) this.error.set(errorMessage(error)); return false; }
    finally { if (request === this.requestId) this.setBusy(false); }
  }
}
