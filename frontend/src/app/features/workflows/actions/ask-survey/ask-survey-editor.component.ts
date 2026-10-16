import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { IconComponent } from '../../../../shared/icon.component';
import { AskSurveyConfig, SurveyAnswerType, SurveyField } from '../../workflow.models';

const invalidReplies: Record<SurveyAnswerType, string> = {
  text: 'Please enter a text answer.', number: 'Please enter a valid number.',
  attachment: 'Please upload one file.', attachments: 'Please upload one or more files.',
  email: 'Please enter a valid email address.', phone: 'Please enter a valid phone number.'
};

export function newSurveyField(): SurveyField {
  return { id: crypto.randomUUID(), question: '', answer_type: 'text', invalid_answer_reply: invalidReplies.text,
    variable_name: '', quick_answers: [], enable_skip: false };
}

export function surveyConfigError(config: AskSurveyConfig): string {
  if (!config.fields.length || config.fields.length > 50) return 'Add between 1 and 50 survey fields.';
  const names = new Set<string>();
  for (const [index, field] of config.fields.entries()) {
    const prefix = `Field #${index + 1}: `;
    if (!field.question.trim() || field.question.length > 4000) return prefix + 'enter a question of up to 4,000 characters.';
    if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(field.variable_name.trim())) return prefix + 'start the variable name with a letter and use letters, numbers, or underscores (up to 64 characters).';
    if (names.has(field.variable_name.trim())) return prefix + 'use a different variable name for each field.';
    names.add(field.variable_name.trim());
    if (!field.invalid_answer_reply.trim() || field.invalid_answer_reply.length > 4000) return prefix + 'enter an invalid answer reply of up to 4,000 characters.';
    if (field.quick_answers.length > 13 || field.quick_answers.some(value => !value.trim() || value.length > 100)) return prefix + 'use up to 13 non-empty quick answers of 100 characters each.';
    if (new Set(field.quick_answers.map(value => value.trim())).size !== field.quick_answers.length) return prefix + 'quick answers must be unique.';
  }
  return '';
}

@Component({
  selector: 'petrix-ask-survey-editor', imports: [FormsModule, IconComponent],
  templateUrl: './ask-survey-editor.component.html', styleUrl: './ask-survey-editor.component.css'
})
export class AskSurveyEditorComponent implements OnChanges {
  @Input({ required: true }) config!: AskSurveyConfig;
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<AskSurveyConfig>();
  value: AskSurveyConfig = { fields: [] };
  expanded = new Set<string>();
  readonly types: { value: SurveyAnswerType; label: string }[] = [
    { value: 'text', label: 'Text' }, { value: 'number', label: 'Number' },
    { value: 'attachment', label: 'Attachment' }, { value: 'attachments', label: 'Attachment(s)' },
    { value: 'email', label: 'Email' }, { value: 'phone', label: 'Phone' }
  ];
  ngOnChanges() {
    const initial = !this.value.fields.length;
    this.value = structuredClone(this.config);
    if (initial && this.value.fields.length) this.expanded.add(this.value.fields[0].id);
  }
  update() { this.configChange.emit(structuredClone(this.value)); }
  toggle(id: string) { this.expanded.has(id) ? this.expanded.delete(id) : this.expanded.add(id); }
  addField() {
    if (this.disabled || this.value.fields.length >= 50) return;
    const field = newSurveyField(); this.value.fields.push(field); this.expanded.add(field.id); this.update();
  }
  removeField(id: string) {
    if (this.disabled) return;
    this.value.fields = this.value.fields.filter(field => field.id !== id); this.expanded.delete(id); this.update();
  }
  setType(field: SurveyField, type: SurveyAnswerType) {
    if (field.invalid_answer_reply === invalidReplies[field.answer_type]) field.invalid_answer_reply = invalidReplies[type];
    field.answer_type = type; this.update();
  }
  addAnswer(field: SurveyField) { if (!this.disabled && field.quick_answers.length < 13) { field.quick_answers.push(''); this.update(); } }
  removeAnswer(field: SurveyField, index: number) { if (!this.disabled) { field.quick_answers.splice(index, 1); this.update(); } }
  badName(field: SurveyField) { return !!field.variable_name && !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(field.variable_name.trim()); }
  duplicateName(field: SurveyField) { return !!field.variable_name.trim() && this.value.fields.filter(value => value.variable_name.trim() === field.variable_name.trim()).length > 1; }
}
