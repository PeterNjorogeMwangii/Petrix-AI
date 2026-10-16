import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { SetVariableConfig } from '../../workflow.models';

export function variableConfigError(config: SetVariableConfig): string {
  if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(config.variable_name)) return 'Start the variable name with a letter and use only letters, numbers, or underscores (up to 64 characters).';
  if (!config.chat_script.trim() || config.chat_script.length > 2000) return 'Enter a Python expression of up to 2,000 characters.';
  return '';
}

@Component({
  selector: 'petrix-set-variable-editor', imports: [FormsModule],
  templateUrl: './set-variable-editor.component.html', styleUrl: './set-variable-editor.component.css'
})
export class SetVariableEditorComponent implements OnChanges {
  @Input({ required: true }) config!: SetVariableConfig;
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<SetVariableConfig>();
  value: SetVariableConfig = { variable_name: '', chat_script: '' };
  readonly examples = ['"PetrixBank"', 'financial_institution + "-" + str(ticket_number)', 'variables.get("attempts", 0) + 1', 'round(amount * 0.16, 2)', 'message.strip().lower()'];
  ngOnChanges() { this.value = structuredClone(this.config); }
  update() { this.configChange.emit(structuredClone(this.value)); }
  nameError() { return this.value.variable_name && !/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(this.value.variable_name); }
}
