import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { IconComponent } from '../../../../shared/icon.component';
import { RatingFaceComponent } from '../../../../shared/rating-face.component';
import { CollectFeedbackConfig, FeedbackField, FeedbackFieldText, FeedbackReplyType, FeedbackTranslation } from '../../workflow.models';

export const defaultRequest = 'How would you rate your conversation?';
export const defaultSubmission = 'Thank you for your feedback!';
export function newFeedbackTranslation(): FeedbackTranslation {
  return { webchat_rating_title: 'Please rate the conversation', rating_request_message: defaultRequest, submission_text: defaultSubmission, fields: {} };
}
export function newFeedbackConfig(language: string): CollectFeedbackConfig {
  return { rating_enabled: true, rating_variable_name: 'feedback_rating', survey_enabled: false, fields: [], translations: { [language]: newFeedbackTranslation() } };
}
export function normalizeFeedbackConfig(config: CollectFeedbackConfig): CollectFeedbackConfig {
  const value = structuredClone(config);
  return { rating_enabled: value.rating_enabled, rating_variable_name: value.rating_variable_name ?? 'feedback_rating',
    survey_enabled: value.survey_enabled ?? false, fields: value.fields ?? [],
    translations: Object.fromEntries(Object.entries(value.translations).map(([code,text]) => [code,{ ...newFeedbackTranslation(), ...text, fields: text.fields ?? {} }])) };
}
const validName = (name: string) => /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(name.trim()) && !['language','system','variables','agent_id','agent_name','conversation_id','message','default_language','attachment_name','attachment_type'].includes(name.trim());
export function feedbackConfigError(config: CollectFeedbackConfig) {
  if (!validName(config.rating_variable_name)) return 'Enter a valid rating variable name (1–64 letters, numbers, or underscores, starting with a letter).';
  if (config.survey_enabled && !config.fields.length) return 'Add at least one survey field or disable the survey.';
  const names = config.rating_enabled ? [config.rating_variable_name.trim()] : [];
  for (const [index,field] of config.fields.entries()) {
    if (!validName(field.variable_name)) return `Field #${index+1}: enter a valid chat variable name.`;
    names.push(field.variable_name.trim());
    if (config.survey_enabled && !Object.values(config.translations).some(text => text.fields[field.id]?.question.trim())) return `Field #${index+1}: enter a question.`;
  }
  if (new Set(names).size !== names.length) return 'Use a different chat variable for the rating and each survey field.';
  if (!config.rating_enabled && !config.survey_enabled) return '';
  if (!Object.keys(config.translations).length) return 'Configure at least one feedback language.';
  for (const text of Object.values(config.translations)) {
    if (config.rating_enabled && (!text.webchat_rating_title.trim() || !text.rating_request_message.trim())) return 'Enter a Webchat rating title and rating request message for each configured feedback language.';
    if (!text.submission_text.trim()) return 'Enter feedback submission text for each configured language.';
    for (const field of config.fields) {
      const question = text.fields[field.id];
      if (question && !question.question.trim()) return 'Enter a question for every configured survey translation.';
      if (config.survey_enabled && field.reply_type === 'choice' && question && (!question.choices.length || question.choices.some(choice => !choice.trim()) || new Set(question.choices.map(choice=>choice.trim())).size !== question.choices.length)) return 'Add distinct, nonempty choices for each translated multiple-choice field.';
    }
  }
  return '';
}

@Component({
  selector: 'petrix-collect-feedback-editor', imports: [FormsModule, IconComponent, RatingFaceComponent],
  templateUrl: './collect-feedback-editor.component.html', styleUrl: './collect-feedback-editor.component.css'
})
export class CollectFeedbackEditorComponent implements OnChanges {
  @Input({ required: true }) config!: CollectFeedbackConfig;
  @Input() language = 'en';
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<CollectFeedbackConfig>();
  value = newFeedbackConfig('en'); text = newFeedbackTranslation(); drafts: Record<string, FeedbackFieldText> = {};
  tab: 'rating' | 'survey' | 'settings' = 'rating'; hasTranslation = true;
  expanded = new Set<string>(); copied = '';
  readonly faces = [1,2,3,4,5];
  readonly tabs = ['rating','survey','settings'] as const;
  readonly types: {value: FeedbackReplyType; label: string}[] = [{value:'open_response',label:'Open response'},{value:'number',label:'Number'},{value:'email',label:'Email'},{value:'phone',label:'Phone'},{value:'choice',label:'Multiple choice'}];
  ngOnChanges() {
    this.value = normalizeFeedbackConfig(this.config); this.hasTranslation = !!this.value.translations[this.language];
    this.text = structuredClone(this.value.translations[this.language] ?? newFeedbackTranslation());
    this.drafts = Object.fromEntries(this.value.fields.map(field=>[field.id,structuredClone(this.text.fields[field.id] ?? {question:'',placeholder:'',choices:[]})]));
  }
  update() { this.configChange.emit(structuredClone(this.value)); }
  updateText() { this.value.translations[this.language] = structuredClone(this.text); this.update(); }
  updateField(id: string) { this.text.fields[id] = structuredClone(this.drafts[id]); this.updateText(); }
  toggle(id: string) { this.expanded.has(id) ? this.expanded.delete(id) : this.expanded.add(id); }
  addField() {
    if (this.disabled || this.value.fields.length >= 50) return;
    let name = 'feedback_comment'; let suffix = 2;
    const names = new Set([this.value.rating_variable_name,...this.value.fields.map(field=>field.variable_name)]);
    while (names.has(name)) name = `feedback_${suffix++}`;
    const field: FeedbackField = {id:crypto.randomUUID(),reply_type:'open_response',required:true,variable_name:name};
    this.value.fields.push(field); this.expanded.add(field.id);
    this.drafts[field.id] = {question:'Do you have any other comments?',placeholder:'Please share your comments',choices:[]};
    this.updateField(field.id);
  }
  removeField(id: string) {
    if (this.disabled) return;
    this.value.fields = this.value.fields.filter(field=>field.id!==id);
    for (const text of Object.values(this.value.translations)) delete text.fields[id];
    delete this.text.fields[id]; delete this.drafts[id]; this.expanded.delete(id); this.update();
  }
  move(index: number, offset: number) {
    const next = index + offset; if (this.disabled || next < 0 || next >= this.value.fields.length) return;
    [this.value.fields[index],this.value.fields[next]] = [this.value.fields[next],this.value.fields[index]]; this.update();
  }
  setType(field: FeedbackField, type: FeedbackReplyType) { field.reply_type = type; this.update(); }
  addChoice(id: string) { if (this.drafts[id].choices.length < 13) { this.drafts[id].choices.push(''); this.updateField(id); } }
  removeChoice(id: string, index: number) { this.drafts[id].choices.splice(index,1); this.updateField(id); }
  async copy(name: string) { try { await navigator.clipboard.writeText(name); this.copied=name; } catch { this.copied=''; } }
  shiftTab(event: Event, offset: number) { event.preventDefault(); this.tab=this.tabs[(this.tabs.indexOf(this.tab)+offset+3)%3]; setTimeout(()=>document.getElementById('feedback-tab-'+this.tab)?.focus()); }
}
