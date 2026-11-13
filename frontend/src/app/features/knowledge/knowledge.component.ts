import { HttpClient } from '@angular/common/http';
import { Component, EventEmitter, Input, OnChanges, OnDestroy, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { ApiService, errorMessage } from '../../core/api.service';
import { Agent } from '../../core/models';
import { IconComponent } from '../../shared/icon.component';

interface KnowledgeSource { id: string; name: string; content: string; enabled: boolean }
interface KnowledgeDocument { agent_id: string; revision: number; sources: KnowledgeSource[]; updated_at: string | null }

@Component({
  selector: 'petrix-knowledge', imports: [FormsModule, IconComponent],
  templateUrl: './knowledge.component.html', styleUrl: './knowledge.component.css'
})
export class KnowledgeComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) agent!: Agent;
  @Input() canManage = false;
  @Output() busyChange = new EventEmitter<boolean>();
  private readonly http = inject(HttpClient);
  private readonly api = inject(ApiService);
  readonly document = signal<KnowledgeDocument | null>(null);
  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly success = signal('');
  sources: KnowledgeSource[] = [];
  private baseline = '[]';
  private generation = 0;
  private loadedAgent = '';
  private path() { return `/api/workspaces/${this.api.context()!.workspace.id}/agents/${this.agent.id}/knowledge`; }
  ngOnChanges() {
    if (this.loadedAgent !== this.agent.id) {
      this.loadedAgent = this.agent.id; this.sources = []; this.baseline = '[]'; this.document.set(null); void this.fetch();
    }
  }
  ngOnDestroy() { ++this.generation; }
  dirty() { return JSON.stringify(this.sources) !== this.baseline; }
  canDiscard() {
    if (this.busy()) return false;
    if (this.dirty() && !window.confirm('Discard the unsaved knowledge changes?')) return false;
    this.sources = structuredClone(this.document()?.sources || []); this.baseline = JSON.stringify(this.sources); return true;
  }
  async load() { if (this.canDiscard()) await this.fetch(); }
  private accept(document: KnowledgeDocument) {
    this.document.set(document); this.sources = structuredClone(document.sources); this.baseline = JSON.stringify(this.sources);
  }
  private async fetch() {
    const generation = ++this.generation; this.loading.set(true); this.error.set(''); this.success.set('');
    try { const result = await firstValueFrom(this.http.get<KnowledgeDocument>(this.path())); if (generation === this.generation) this.accept(result); }
    catch (error) { if (generation === this.generation) this.error.set(errorMessage(error)); }
    finally { if (generation === this.generation) this.loading.set(false); }
  }
  add() {
    if (!this.canManage || this.busy() || this.sources.length >= 20) return;
    this.sources = [...this.sources, { id: crypto.randomUUID(), name: '', content: '', enabled: true }]; this.success.set('');
  }
  remove(id: string) { if (this.canManage && !this.busy()) { this.sources = this.sources.filter(source => source.id !== id); this.success.set(''); } }
  bytes() { return this.sources.reduce((total, source) => total + new TextEncoder().encode(source.name + source.content).length, 0); }
  validationError() {
    if (this.sources.some(source => !source.name.trim() || !source.content.trim())) return 'Give each source a title and some text, or remove it.';
    if (this.bytes() > 65536) return 'Keep the combined knowledge text within 64 KB.';
    return '';
  }
  async save() {
    const document = this.document(); if (!document || !this.canManage || this.busy() || !this.dirty() || this.validationError()) return;
    const generation = this.generation; this.busy.set(true); this.busyChange.emit(true); this.error.set(''); this.success.set('');
    try {
      const sources = this.sources.map(source => ({ ...source, name: source.name.trim(), content: source.content.trim() }));
      const result = await firstValueFrom(this.http.post<KnowledgeDocument>(this.path(), { expected_revision: document.revision, sources }));
      if (generation === this.generation) { this.accept(result); this.success.set('Knowledge saved. LLM Processing can use the enabled sources.'); }
    } catch (error) { if (generation === this.generation) this.error.set(errorMessage(error)); }
    finally { this.busy.set(false); this.busyChange.emit(false); }
  }
}
