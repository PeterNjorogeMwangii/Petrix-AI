import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { errorMessage } from '../../../../core/api.service';
import { AttachmentPreviewComponent } from '../../attachment-preview.component';
import { CarouselImage, WorkflowAssetMetadata } from '../../workflow.models';
import { WorkflowsApi } from '../../workflows.api';

@Component({
  selector: 'petrix-carousel-image-editor', imports: [FormsModule, AttachmentPreviewComponent],
  template: `
    <div class="field"><label [for]="'image-source-' + cardId">Image <span>Optional</span></label><select [id]="'image-source-' + cardId" [ngModel]="value?.source || 'none'" (ngModelChange)="changeSource($event)" [ngModelOptions]="{standalone:true}" [disabled]="disabled || uploading()"><option value="none">No image</option><option value="upload">Upload image</option><option value="link">Image link</option></select></div>
    @if (value?.source === 'upload') {
      <input #picker type="file" accept=".jpg,.jpeg,.png,.gif" [attr.aria-label]="'Choose image for card ' + cardNumber" [disabled]="disabled || uploading()" (change)="select($event)" class="sr-only">
      @if (file(); as uploaded) { <petrix-attachment-preview [file]="uploaded" /> }
      <div class="upload-box" (dragover)="$event.preventDefault()" (drop)="drop($event)"><button type="button" [disabled]="disabled || uploading()" (click)="picker.click()">{{ uploading() ? 'Uploading image…' : file() ? 'Replace image' : loading() ? 'Loading image…' : 'Drop an image here or browse files' }}</button><small>JPG, JPEG, PNG, GIF · Up to 20 MiB</small></div>
    } @else if (value?.source === 'link') {
      <div class="field"><label [for]="'image-url-' + cardId">Image URL</label><input [id]="'image-url-' + cardId" type="url" maxlength="2048" [ngModel]="value?.url" (ngModelChange)="updateUrl($event)" [ngModelOptions]="{standalone:true}" [disabled]="disabled" placeholder="https://example.com/image.jpg"></div>
    }
    @if (error()) { <p class="field-error" role="alert">{{ error() }}</p> }
  `,
  styles: [`:host{display:block}.field{margin:12px 0}label{display:block;font-size:12px;margin-bottom:8px}label span{font-size:10px;color:var(--muted);margin-left:4px}input,select{width:100%;background:#121214;color:var(--text);border:1px solid #39393e;border-radius:7px;padding:10px;font-size:12px}.upload-box{padding:16px 10px;border:1px dashed #48484f;border-radius:8px;text-align:center;margin:10px 0 16px}.upload-box button{color:var(--accent);background:none;border:0;font-size:12px}.upload-box small{display:block;color:var(--muted);font-size:10px;margin-top:8px}button:disabled{opacity:.5}`]
})
export class CarouselImageEditorComponent implements OnChanges, OnDestroy {
  @Input() image: CarouselImage | null = null;
  @Input({ required: true }) agentId!: string;
  @Input({ required: true }) cardId!: string;
  @Input() cardNumber = 1;
  @Input() disabled = false;
  @Output() imageChange = new EventEmitter<CarouselImage | null>();
  @Output() uploadBusyChange = new EventEmitter<boolean>();
  @Output() assetUploaded = new EventEmitter<string>();
  private readonly api = inject(WorkflowsApi);
  readonly file = signal<WorkflowAssetMetadata | null>(null);
  readonly uploading = signal(false);
  readonly loading = signal(false);
  readonly error = signal('');
  value: CarouselImage | null = null;
  private key = ''; private request = 0; private alive = true;
  ngOnChanges() {
    this.value = structuredClone(this.image);
    const key = `${this.agentId}:${this.value?.source}:${this.value?.asset_id}`;
    if (key === this.key) return;
    this.key = key; this.file.set(null); this.error.set(''); this.loading.set(false); const request = ++this.request;
    if (this.value?.source === 'upload' && this.value.asset_id) {
      this.loading.set(true);
      void this.api.asset(this.agentId, this.value.asset_id).then(file => { if (this.alive && request === this.request) this.file.set(file); })
        .catch(error => { if (this.alive && request === this.request) this.error.set(errorMessage(error)); })
        .finally(() => { if (this.alive && request === this.request) this.loading.set(false); });
    }
  }
  ngOnDestroy() { this.alive = false; ++this.request; }
  changeSource(source: 'none' | 'upload' | 'link') {
    this.error.set(''); this.imageChange.emit(source === 'none' ? null : { source, asset_id: null, url: '' });
  }
  updateUrl(url: string) { if (this.value) this.imageChange.emit({ ...this.value, url }); }
  select(event: Event) { const input = event.target as HTMLInputElement; const files = Array.from(input.files || []); input.value = ''; void this.upload(files); }
  drop(event: DragEvent) { event.preventDefault(); event.stopPropagation(); void this.upload(Array.from(event.dataTransfer?.files || [])); }
  private async upload(files: File[]) {
    if (this.disabled || this.uploading()) return;
    this.error.set(''); const file = files[0];
    if (files.length !== 1 || !file || !/\.(jpe?g|png|gif)$/i.test(file.name)) { this.error.set('Choose one JPG, JPEG, PNG, or GIF image.'); return; }
    if (!file.size || file.size > 20971520) { this.error.set('Choose a non-empty image, at most 20 MiB.'); return; }
    const agentId = this.agentId; this.uploading.set(true); this.uploadBusyChange.emit(true);
    try {
      const result = await this.api.uploadAsset(agentId, file, 'image');
      if (!this.alive || agentId !== this.agentId) { void this.api.deleteAsset(agentId, result.id).catch(() => {}); return; }
      this.assetUploaded.emit(result.id); this.imageChange.emit({ source: 'upload', asset_id: result.id, url: '' });
    } catch (error) { if (this.alive) this.error.set(errorMessage(error)); }
    finally { if (this.alive) { this.uploading.set(false); this.uploadBusyChange.emit(false); } }
  }
}
