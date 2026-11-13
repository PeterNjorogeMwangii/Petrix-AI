import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { errorMessage } from '../../../../core/api.service';
import { IconComponent } from '../../../../shared/icon.component';
import { AttachmentPreviewComponent } from '../../attachment-preview.component';
import { AttachmentFormat, AttachmentTranslation, SendAttachmentConfig, WorkflowAssetMetadata } from '../../workflow.models';
import { WorkflowsApi } from '../../workflows.api';

export const ATTACHMENT_EXTENSIONS: Record<AttachmentFormat, string[]> = {
  image: ['jpg', 'jpeg', 'png', 'gif'], audio: ['mp3', 'm4a', 'wav'], video: ['mp4', 'avi', 'mov'],
  document: ['pdf', 'csv', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx']
};
export function newAttachmentTranslation(): AttachmentTranslation {
  return { file_format: 'image', source: 'upload', asset_id: null, url: '', chat_script: '' };
}
export function sendAttachmentConfigError(config: SendAttachmentConfig) {
  if (!Object.keys(config.translations).length) return 'Configure at least one attachment.';
  for (const [code, item] of Object.entries(config.translations)) {
    if (item.source === 'upload' && !item.asset_id) return `Upload a file for ${code} before saving.`;
    if (item.source === 'script' && !item.chat_script.trim()) return `Enter a file URL expression for ${code}.`;
    if (item.source === 'link') {
      try {
        const value = item.url; const url = new URL(value);
        if (value.length > 2048 || !/^https?:\/\//.test(value) || !url.hostname || url.username || url.password || url.hash || /[\s\\{}\x00-\x1f]/.test(value)) throw new Error();
        const name = decodeURIComponent(url.pathname.split('/').pop() || '');
        const suffix = name.includes('.') ? name.split('.').pop()!.toLowerCase() : '';
        if (Object.values(ATTACHMENT_EXTENSIONS).some(values => values.includes(suffix)) && !ATTACHMENT_EXTENSIONS[item.file_format].includes(suffix)) return `The file link for ${code} does not match the selected format.`;
      } catch { return `Enter a complete HTTP or HTTPS file URL for ${code}.`; }
    }
  }
  return '';
}

@Component({
  selector: 'petrix-send-attachment-editor', imports: [FormsModule, IconComponent, AttachmentPreviewComponent],
  templateUrl: './send-attachment-editor.component.html', styleUrl: './send-attachment-editor.component.css'
})
export class SendAttachmentEditorComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) config!: SendAttachmentConfig;
  @Input({ required: true }) agentId!: string;
  @Input() language = 'en';
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<SendAttachmentConfig>();
  @Output() uploadBusyChange = new EventEmitter<boolean>();
  @Output() assetUploaded = new EventEmitter<string>();
  private readonly api = inject(WorkflowsApi);
  readonly file = signal<WorkflowAssetMetadata | null>(null);
  readonly error = signal('');
  readonly uploading = signal(false);
  readonly loading = signal(false);
  dragging = false;
  hasTranslation = false;
  translation = newAttachmentTranslation();
  readonly formats: { value: AttachmentFormat; label: string }[] = [
    { value: 'image', label: 'Image' }, { value: 'audio', label: 'Audio' }, { value: 'video', label: 'Video' }, { value: 'document', label: 'Document' }
  ];
  private fileKey = '';
  private request = 0;
  private alive = true;
  ngOnChanges() {
    this.hasTranslation = !!this.config.translations[this.language];
    this.translation = structuredClone(this.config.translations[this.language] || newAttachmentTranslation());
    const key = `${this.agentId}:${this.language}:${this.translation.source}:${this.translation.asset_id || ''}`;
    if (key === this.fileKey) return;
    this.fileKey = key; this.error.set(''); this.file.set(null); this.loading.set(false);
    const request = ++this.request;
    if (this.translation.source === 'upload' && this.translation.asset_id) {
      this.loading.set(true);
      void this.api.asset(this.agentId, this.translation.asset_id).then(file => {
        if (this.alive && request === this.request) this.file.set(file);
      }).catch(error => { if (this.alive && request === this.request) this.error.set(errorMessage(error)); })
        .finally(() => { if (this.alive && request === this.request) this.loading.set(false); });
    }
  }
  ngOnDestroy() { this.alive = false; ++this.request; }
  update() { this.configChange.emit({ translations: { ...this.config.translations, [this.language]: structuredClone(this.translation) } }); }
  changeFormat(format: AttachmentFormat) {
    this.translation.file_format = format; this.translation.asset_id = null; this.file.set(null); this.update();
  }
  changeSource(source: AttachmentTranslation['source']) {
    this.translation = { ...this.translation, source, asset_id: null, url: '', chat_script: '' }; this.file.set(null); this.update();
  }
  extensions() { return ATTACHMENT_EXTENSIONS[this.translation.file_format].join(', '); }
  accept() { return ATTACHMENT_EXTENSIONS[this.translation.file_format].map(value => '.' + value).join(','); }
  remove() { this.translation.asset_id = null; this.file.set(null); this.error.set(''); this.update(); }
  select(event: Event) {
    const input = event.target as HTMLInputElement; const files = Array.from(input.files || []); input.value = '';
    void this.upload(files);
  }
  dragOver(event: DragEvent) { event.preventDefault(); if (!this.disabled && !this.uploading()) this.dragging = true; }
  drop(event: DragEvent) { event.preventDefault(); this.dragging = false; void this.upload(Array.from(event.dataTransfer?.files || [])); }
  async upload(files: File[]) {
    if (this.disabled || this.uploading()) return;
    this.error.set('');
    if (files.length !== 1) { this.error.set('Choose one file at a time.'); return; }
    const file = files[0]; const suffix = file.name.split('.').pop()?.toLowerCase() || '';
    if (!ATTACHMENT_EXTENSIONS[this.translation.file_format].includes(suffix)) { this.error.set('Choose a file matching the selected format.'); return; }
    if (!file.size || file.size > 20971520) { this.error.set('Choose a non-empty file, at most 20 MiB.'); return; }
    const agentId = this.agentId; const language = this.language; const format = this.translation.file_format;
    this.uploading.set(true); this.uploadBusyChange.emit(true);
    try {
      const result = await this.api.uploadAsset(agentId, file, format);
      if (!this.alive || agentId !== this.agentId || language !== this.language) {
        void this.api.deleteAsset(agentId, result.id).catch(() => {}); return;
      }
      this.assetUploaded.emit(result.id);
      this.translation.asset_id = result.id; this.update();
    } catch (error) { if (this.alive) this.error.set(errorMessage(error)); }
    finally { if (this.alive) { this.uploading.set(false); this.uploadBusyChange.emit(false); } }
  }
}
