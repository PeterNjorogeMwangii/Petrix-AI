import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { LLMProcessingConfig, LLMProcessingOptions } from '../../workflow.models';

export function newLLMProcessingConfig(): LLMProcessingConfig {
  return { input: '', input_mode: 'template', model: 'intent', temperature: 1,
    system_prompt: 'You are a helpful assistant.', system_prompt_mode: 'template',
    use_knowledge_info: false, variable_name: '', timeout_seconds: 30,
    success_path_id: crypto.randomUUID(), error_path_id: crypto.randomUUID(), timeout_path_id: crypto.randomUUID() };
}
export function llmProcessingConfigError(config: LLMProcessingConfig): string {
  if (!config.input.trim()) return 'Enter the content to process.';
  if (config.input.length > (config.input_mode === 'expression' ? 2000 : 16000)) return 'The input is too long.';
  if (config.system_prompt.length > (config.system_prompt_mode === 'expression' ? 2000 : 8000)) return 'The system prompt is too long.';
  if (config.system_prompt_mode === 'expression' && !config.system_prompt.trim()) return 'Enter a Python expression for the system prompt, or switch to text.';
  if (typeof config.temperature !== 'number' || !Number.isFinite(config.temperature) || config.temperature < 0 || config.temperature > 2) return 'Choose a temperature from 0 to 2.';
  if (!Number.isInteger(config.timeout_seconds) || config.timeout_seconds < 1 || config.timeout_seconds > 120) return 'Choose a timeout from 1 to 120 seconds.';
  if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(config.variable_name) || ['language', 'system', 'agent_id', 'agent_name', 'default_language', 'conversation_id', 'message', 'attachment_name', 'attachment_type', 'variables'].includes(config.variable_name)) return 'Use a custom variable name starting with a letter, followed by letters, numbers, or underscores (up to 64 characters).';
  return '';
}
@Component({
  selector: 'petrix-llm-processing-editor', imports: [FormsModule],
  templateUrl: './llm-processing-editor.component.html', styleUrl: './llm-processing-editor.component.css'
})
export class LLMProcessingEditorComponent implements OnChanges {
  @Input({ required: true }) config!: LLMProcessingConfig;
  @Input() options: LLMProcessingOptions | null = null;
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<LLMProcessingConfig>();
  value = newLLMProcessingConfig();
  ngOnChanges() { this.value = structuredClone(this.config); }
  update() { this.configChange.emit(structuredClone(this.value)); }
  error() { return llmProcessingConfigError(this.value); }
  toggle(field: 'input_mode' | 'system_prompt_mode') { this.value[field] = this.value[field] === 'template' ? 'expression' : 'template'; this.update(); }
}
