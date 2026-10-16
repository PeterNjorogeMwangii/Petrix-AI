import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MessageTranslation, SendMessageConfig } from '../../workflow.models';

@Component({
  selector: 'petrix-send-message-editor',
  imports: [FormsModule],
  templateUrl: './send-message-editor.component.html',
  styleUrl: './send-message-editor.component.css'
})
export class SendMessageEditorComponent implements OnChanges {
  @Input({ required: true }) config!: SendMessageConfig;
  @Input() language = 'en';
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<SendMessageConfig>();
  translation: MessageTranslation = { messages: [''], quick_replies: [] };
  hasTranslation = false;

  ngOnChanges() {
    this.hasTranslation = !!this.config?.translations?.[this.language];
    this.translation = structuredClone(this.config?.translations?.[this.language] || { messages: [''], quick_replies: [] });
  }
  update() {
    this.configChange.emit({ translations: { ...this.config.translations, [this.language]: structuredClone(this.translation) } });
  }
  addMessage() { if (this.translation.messages.length < 10) { this.translation.messages.push(''); this.update(); } }
  removeMessage(index: number) { if (this.translation.messages.length > 1) { this.translation.messages.splice(index, 1); this.update(); } }
  addReply() { if (this.translation.quick_replies.length < 13) { this.translation.quick_replies.push(''); this.update(); } }
  removeReply(index: number) { this.translation.quick_replies.splice(index, 1); this.update(); }
}
