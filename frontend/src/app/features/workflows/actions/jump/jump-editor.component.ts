import { Component, ElementRef, EventEmitter, Input, Output, ViewChild } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { IconComponent } from '../../../../shared/icon.component';
import { JumpConfig } from '../../workflow.models';
import { JumpTarget, jumpConfigError } from './jump-targets';

@Component({
  selector: 'petrix-jump-editor', imports: [FormsModule, IconComponent],
  templateUrl: './jump-editor.component.html', styleUrl: './jump-editor.component.css'
})
export class JumpEditorComponent {
  @Input({ required: true }) config!: JumpConfig;
  @Input({ required: true }) targets: JumpTarget[] = [];
  @Input() disabled = false;
  @Input() active = true;
  @Output() configChange = new EventEmitter<JumpConfig>();
  @ViewChild('targetSearch') searchInput?: ElementRef<HTMLInputElement>;
  open = false;
  search = '';

  selected() { return this.targets.find(target => target.id === this.config.target_node_id); }
  filtered() {
    const query = this.search.trim().toLocaleLowerCase();
    if (/^#?\d+$/.test(query)) return this.targets.filter(target => String(target.number) === query.replace(/^#/, ''));
    return this.targets.filter(target => !query || `#${target.number} ${target.name} ${target.path} ${target.kind} ${target.type.replaceAll('_', ' ')}`.toLocaleLowerCase().includes(query));
  }
  toggle() {
    if (this.disabled) return;
    this.open = !this.open; this.search = '';
    if (this.open) setTimeout(() => this.searchInput?.nativeElement.focus());
  }
  choose(target: JumpTarget) {
    if (this.disabled || (this.active && !target.enabled)) return;
    this.configChange.emit({ target_node_id: target.id }); this.open = false; this.search = '';
  }
  clear() { if (!this.disabled) { this.configChange.emit({ target_node_id: '' }); this.open = false; } }
  error() { return jumpConfigError(this.config, this.targets, this.active); }
  icon(target: JumpTarget) {
    return target.type === 'send_message' || target.type === 'message_received' ? 'chat'
      : target.type === 'set_variable' ? 'variable' : target.type === 'jump' ? 'jump'
      : target.type === 'fallback' ? 'help' : target.type === 'attachment_received' ? 'upload' : 'spark';
  }
}
