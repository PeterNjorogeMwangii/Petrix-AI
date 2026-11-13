import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { IconComponent } from '../../../../shared/icon.component';
import { BranchCondition, BranchConfig, BranchPath } from '../../workflow.models';

export function newBranchConfig(): BranchConfig {
  return { conditions: [{ id: crypto.randomUUID(), name: '', chat_script: '' }], else_path_id: crypto.randomUUID() };
}

export function branchConfigError(config: BranchConfig): string {
  if (!config.conditions.length || config.conditions.length > 10) return 'Add between 1 and 10 conditions.';
  for (const [index, condition] of config.conditions.entries()) {
    if (!condition.name.trim() || condition.name.length > 100) return `Condition ${index + 1}: enter a name of up to 100 characters.`;
    if (!condition.chat_script.trim() || condition.chat_script.length > 2000) return `Condition ${index + 1}: enter a Python expression of up to 2,000 characters.`;
  }
  return '';
}

@Component({
  selector: 'petrix-branch-editor', imports: [FormsModule, IconComponent],
  templateUrl: './branch-editor.component.html', styleUrl: './branch-editor.component.css'
})
export class BranchEditorComponent implements OnChanges {
  @Input({ required: true }) config!: BranchConfig;
  @Input() paths: BranchPath[] = [];
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<BranchConfig>();
  value: BranchConfig = { conditions: [], else_path_id: '' };
  ngOnChanges() { this.value = structuredClone(this.config); }
  update() { this.configChange.emit(structuredClone(this.value)); }
  addCondition() {
    if (this.disabled || this.value.conditions.length >= 10) return;
    this.value.conditions.push({ id: crypto.randomUUID(), name: '', chat_script: '' }); this.update();
  }
  removeCondition(condition: BranchCondition) {
    if (this.disabled || this.value.conditions.length <= 1) return;
    if (this.paths.some(path => path.id === condition.id && path.children.length)
      && !window.confirm(`Delete condition “${condition.name}” and all its nested steps?`)) return;
    this.value.conditions = this.value.conditions.filter(value => value.id !== condition.id); this.update();
  }
  moveCondition(index: number, direction: number) {
    const next = index + direction;
    if (this.disabled || !this.value.conditions[next]) return;
    [this.value.conditions[index], this.value.conditions[next]] = [this.value.conditions[next], this.value.conditions[index]];
    this.update();
  }
}
