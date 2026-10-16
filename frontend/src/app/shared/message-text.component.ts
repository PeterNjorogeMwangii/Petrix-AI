import { Component, Input, OnChanges } from '@angular/core';

interface MessagePart { text: string; bold: boolean }

/** Render **bold** using text nodes so message content never becomes raw HTML. */
@Component({
  selector: 'petrix-message-text',
  template: '@for (part of parts; track $index) { @if (part.bold) { <strong>{{ part.text }}</strong> } @else { <span>{{ part.text }}</span> } }',
  styles: [':host { display: contents; } strong { font-weight: 700; }']
})
export class MessageTextComponent implements OnChanges {
  @Input() text = '';
  parts: MessagePart[] = [];

  ngOnChanges() {
    const parts: MessagePart[] = [];
    const bold = /\*\*([\s\S]+?)\*\*/g;
    let position = 0;
    let match: RegExpExecArray | null;
    while ((match = bold.exec(this.text)) !== null) {
      if (match.index > position) parts.push({ text: this.text.slice(position, match.index), bold: false });
      parts.push({ text: match[1], bold: true });
      position = match.index + match[0].length;
    }
    if (position < this.text.length) parts.push({ text: this.text.slice(position), bold: false });
    this.parts = parts;
  }
}
