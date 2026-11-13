import { Component, EventEmitter, Input, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SwitchLanguageConfig } from '../../workflow.models';

export function switchLanguageConfigError(config: SwitchLanguageConfig, languages: string[]): string {
  return languages.includes(config.target_language) ? '' : 'Select a target language already configured in this agent’s settings.';
}

@Component({
  selector: 'petrix-switch-language-editor', imports: [FormsModule],
  templateUrl: './switch-language-editor.component.html', styleUrl: './switch-language-editor.component.css'
})
export class SwitchLanguageEditorComponent {
  @Input({ required: true }) config!: SwitchLanguageConfig;
  @Input({ required: true }) languages: { code: string; name: string }[] = [];
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<SwitchLanguageConfig>();
  unavailable() { return !!this.config.target_language && !this.languages.some(value => value.code === this.config.target_language); }
  select(code: string) { if (!this.disabled) this.configChange.emit({ target_language: code }); }
}
