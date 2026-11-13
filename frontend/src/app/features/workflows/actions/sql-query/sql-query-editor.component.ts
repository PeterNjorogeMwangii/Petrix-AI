import { Component, EventEmitter, Input, OnChanges, OnDestroy, OnInit, Output, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService, errorMessage } from '../../../../core/api.service';
import { IconComponent } from '../../../../shared/icon.component';
import { ConnectionOption, DatabaseConnectionsApi } from '../../../settings/database-connections.api';
import { SQLQueryConfig } from '../../workflow.models';

export function newSQLQueryConfig(): SQLQueryConfig {
  return { connection_id: '', query: '', query_mode: 'template', timeout_seconds: 30, variable_name: '',
    success_path_id: crypto.randomUUID(), error_path_id: crypto.randomUUID(), timeout_path_id: crypto.randomUUID() };
}
export function sqlQueryConfigError(config: SQLQueryConfig): string {
  if (!config.connection_id) return 'Select a verified database connector.';
  if (!config.query.trim()) return 'Enter a read-only SELECT query.';
  if (config.query.length > (config.query_mode === 'expression' ? 2000 : 8000)) return 'The query is too long.';
  if (config.query_mode === 'template' && !/^\s*(?:--[^\n]*\n\s*|\/\*[\s\S]*?\*\/\s*)*select\b/i.test(config.query)) return 'Only SELECT queries are allowed.';
  if (!Number.isInteger(config.timeout_seconds) || config.timeout_seconds < 1 || config.timeout_seconds > 120) return 'Choose a timeout from 1 to 120 seconds.';
  if (!/^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(config.variable_name) || config.variable_name === 'language') return 'Use a custom variable name starting with a letter, followed by letters, numbers, or underscores (up to 64 characters).';
  return '';
}
@Component({
  selector: 'petrix-sql-query-editor', imports: [FormsModule, RouterLink, IconComponent],
  templateUrl: './sql-query-editor.component.html', styleUrl: './sql-query-editor.component.css'
})
export class SQLQueryEditorComponent implements OnInit, OnChanges, OnDestroy {
  @Input({ required: true }) config!: SQLQueryConfig;
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<SQLQueryConfig>();
  private readonly api = inject(DatabaseConnectionsApi);
  readonly auth = inject(ApiService);
  readonly connections = signal<ConnectionOption[]>([]);
  readonly loading = signal(true);
  readonly loadError = signal('');
  value = newSQLQueryConfig();
  private generation = 0;
  ngOnInit() { if (this.auth.context()?.workspace.permissions.manage_ai_agents) void this.load(); else this.loading.set(false); }
  ngOnChanges() { this.value = structuredClone(this.config); }
  ngOnDestroy() { ++this.generation; }
  async load() {
    const generation = ++this.generation; this.loading.set(true); this.loadError.set('');
    try { const data = await this.api.options(); if (generation === this.generation) this.connections.set(data.connections); }
    catch (error) { if (generation === this.generation) this.loadError.set(errorMessage(error)); }
    finally { if (generation === this.generation) this.loading.set(false); }
  }
  unavailable() { return !!this.value.connection_id && !this.connections().some(item => item.id === this.value.connection_id); }
  update() { this.configChange.emit(structuredClone(this.value)); }
  error() { return sqlQueryConfigError(this.value); }
}
