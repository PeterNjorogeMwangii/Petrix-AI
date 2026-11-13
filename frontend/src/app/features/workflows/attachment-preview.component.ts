import { Component, Input, OnChanges } from '@angular/core';
import { IconComponent } from '../../shared/icon.component';
import { SurveyAttachment } from './workflow.models';

@Component({
  selector: 'petrix-attachment-preview', imports: [IconComponent],
  template: `
    @if (file.format === 'image' && !failed) {
      <a class="image-link" [href]="previewUrl()" target="_blank" rel="noopener noreferrer"><img [src]="previewUrl()" [alt]="file.name" loading="lazy" (error)="failed = true"></a>
    } @else if (file.format === 'audio') {
      <audio controls preload="metadata" [src]="previewUrl()" [attr.aria-label]="file.name"></audio>
    } @else if (file.format === 'video') {
      <video controls playsinline preload="metadata" [src]="previewUrl()" [attr.aria-label]="file.name"></video>
    }
    <a class="file-card" [href]="file.url" [attr.download]="file.external ? null : file.name" [attr.target]="file.external ? '_blank' : null" rel="noopener noreferrer">
      <petrix-icon name="paperclip" /><span><strong>{{ file.name }}</strong><small>{{ sizeLabel() }} · {{ file.external ? 'Open file' : 'Download' }}</small></span><petrix-icon name="arrow" />
    </a>
    @if (failed) { <p class="preview-note">Preview unavailable. Open or download the file to view it.</p> }
  `,
  styles: [`
    :host{display:block;min-width:0;max-width:100%}img,video{display:block;width:100%;max-height:260px;object-fit:contain;border-radius:8px;background:#0e0e10;margin-bottom:8px}audio{width:100%;max-width:100%;margin:4px 0 8px;height:42px}
    .image-link{display:block}.file-card{display:flex;align-items:center;gap:10px;padding:12px;border:1px solid #45454a;border-radius:9px;color:var(--text);text-decoration:none;background:#ffffff04;min-width:0}.file-card:hover{border-color:var(--accent)}.file-card>span{flex:1;min-width:0}.file-card strong{display:block;overflow-wrap:anywhere;font-size:12px;font-weight:500}.file-card small{display:block;font-size:10px;color:var(--muted);margin-top:5px}.file-card petrix-icon{flex:none;width:16px}.preview-note{font-size:11px;color:var(--muted);line-height:1.5;margin-top:8px}
  `]
})
export class AttachmentPreviewComponent implements OnChanges {
  @Input({ required: true }) file!: SurveyAttachment;
  failed = false;
  ngOnChanges() { this.failed = false; }
  previewUrl() { return this.file.external ? this.file.url : `${this.file.url}?inline=true`; }
  sizeLabel() {
    const size = this.file.size_bytes;
    return size === null ? 'Hosted file' : size < 1024 ? `${size} B` : size < 1048576 ? `${Math.round(size / 1024)} KiB` : `${(size / 1048576).toFixed(1)} MiB`;
  }
}
