import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { IconComponent } from '../../../../shared/icon.component';
import { APIMethod, SendAPIConfig } from '../../workflow.models';

const managedHeaders = new Set(['host', 'content-length', 'transfer-encoding', 'connection', 'proxy-connection',
  'proxy-authorization', 'upgrade', 'te', 'trailer', 'accept-encoding']);

export function newSendAPIConfig(): SendAPIConfig {
  return { method: 'GET', url: '', headers: [], payload_mode: 'json', payload: '', variable_name: '', timeout_seconds: 30,
    success_path_id: crypto.randomUUID(), error_path_id: crypto.randomUUID(), timeout_path_id: crypto.randomUUID() };
}

export function sendAPIConfigError(config: SendAPIConfig): string {
  try {
    const sample = config.url.replace(/\{\{?\s*[A-Za-z][A-Za-z0-9_.]*\s*\}\}?/g, 'value');
    const url = new URL(sample);
    if (!config.url.startsWith('https://') || url.protocol !== 'https:' || !url.hostname || url.username || url.password
      || sample.includes('#') || /[\s\\{}]/.test(sample) || /[{}]/.test(config.url.split('/').slice(0,3).join('/'))) throw new Error();
  } catch { return 'Enter an HTTPS endpoint. Variables can be used in its path or query.'; }
  if (config.url.length > 2048) return 'The endpoint can contain up to 2,048 characters.';
  if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(config.variable_name)) return 'Start the resulting variable with a letter and use letters, numbers, or underscores (up to 64 characters).';
  if (config.variable_name === 'language') return 'Use a custom variable name instead of language.';
  if (config.headers.length > 20) return 'Use up to 20 headers.';
  const keys = new Set<string>();
  for (const header of config.headers) {
    if (!/^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,100}$/.test(header.key) || managedHeaders.has(header.key.toLowerCase())) return 'Enter a valid header name. Transport headers are managed automatically.';
    if (keys.has(header.key.toLowerCase())) return 'Header names must be unique.';
    keys.add(header.key.toLowerCase());
    if (header.value.length > 4000 || /[^\x20-\x7E]/.test(header.value)) return 'Header values must be printable text of up to 4,000 characters without line breaks.';
  }
  if (!Number.isInteger(config.timeout_seconds) || config.timeout_seconds < 1 || config.timeout_seconds > 60) return 'Choose a timeout from 1 to 60 seconds.';
  if (config.method !== 'GET' && config.payload.trim()) {
    if (config.payload_mode === 'json') {
      if (config.payload.length > 16000) return 'The JSON payload can contain up to 16,000 characters.';
      try { JSON.parse(config.payload); } catch { return 'Enter valid JSON, or choose Python expression.'; }
    } else if (config.payload.length > 2000) return 'Python expressions can contain up to 2,000 characters.';
  }
  return '';
}

@Component({
  selector: 'petrix-send-api-editor', imports: [FormsModule, IconComponent],
  templateUrl: './send-api-editor.component.html', styleUrl: './send-api-editor.component.css'
})
export class SendAPIEditorComponent implements OnChanges {
  @Input({ required: true }) config!: SendAPIConfig;
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<SendAPIConfig>();
  value: SendAPIConfig = newSendAPIConfig();
  readonly methods: APIMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'];
  visible = new Set<number>();
  ngOnChanges() { this.value = structuredClone(this.config); }
  update() { this.configChange.emit(structuredClone(this.value)); }
  addHeader() {
    if (!this.disabled && this.value.headers.length < 20) { this.value.headers.push({ key: '', value: '' }); this.update(); }
  }
  removeHeader(index: number) {
    if (this.disabled) return;
    this.value.headers.splice(index, 1); this.visible.clear(); this.update();
  }
  secret(key: string) { return /authorization|token|secret|api[-_]?key|cookie/i.test(key); }
  toggleValue(index: number) { this.visible.has(index) ? this.visible.delete(index) : this.visible.add(index); }
  error() { return sendAPIConfigError(this.value); }
}
