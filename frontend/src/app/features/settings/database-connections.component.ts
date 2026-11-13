import { DatePipe } from '@angular/common';
import { Component, ElementRef, HostListener, OnDestroy, OnInit, ViewChild, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, errorMessage } from '../../core/api.service';
import { IconComponent } from '../../shared/icon.component';
import { ConnectionForm, DatabaseConnection, DatabaseConnectionsApi } from './database-connections.api';

function emptyForm(): ConnectionForm {
  return { name: '', type: 'postgresql', host: '', port: 5432, database: '', username: '', password: '', ssl_mode: 'verify-full' };
}

@Component({
  selector: 'petrix-database-connections', imports: [FormsModule, DatePipe, IconComponent],
  templateUrl: './database-connections.component.html', styleUrl: './database-connections.component.css'
})
export class DatabaseConnectionsComponent implements OnInit, OnDestroy {
  private readonly api = inject(DatabaseConnectionsApi);
  readonly auth = inject(ApiService);
  @ViewChild('connectionDialog') dialog?: ElementRef<HTMLDialogElement>;
  readonly connections = signal<DatabaseConnection[]>([]);
  readonly loading = signal(true);
  readonly busy = signal('');
  readonly error = signal('');
  readonly info = signal('');
  readonly modalError = signal('');
  readonly open = signal(false);
  editing: DatabaseConnection | null = null;
  value = emptyForm();
  private baseline = '';
  private generation = 0;
  canManage() { return ['owner', 'admin'].includes(this.auth.context()?.workspace.role || ''); }
  ngOnInit() { void this.load(); }
  ngOnDestroy() { ++this.generation; this.value.password = ''; this.dialog?.nativeElement.close(); }
  dirty() { return this.open() && JSON.stringify(this.value) !== this.baseline; }
  canLeave() { return !this.busy() && (!this.dirty() || window.confirm('Discard the unsaved database connection?')); }
  @HostListener('window:beforeunload', ['$event']) beforeUnload(event: BeforeUnloadEvent) { if (this.dirty()) { event.preventDefault(); event.returnValue = ''; } }
  async load() {
    const generation = ++this.generation;
    this.loading.set(true); this.error.set('');
    if (!this.canManage()) { this.loading.set(false); return; }
    try { const data = await this.api.list(); if (generation === this.generation) this.connections.set(data.connections); }
    catch (error) { if (generation === this.generation) this.error.set(errorMessage(error)); }
    finally { if (generation === this.generation) this.loading.set(false); }
  }
  edit(connection: DatabaseConnection | null = null) {
    if (this.busy() || !this.canManage()) return;
    this.editing = connection;
    this.value = connection ? { name: connection.name, type: connection.type, host: connection.host, port: connection.port,
      database: connection.database, username: connection.username, ssl_mode: connection.ssl_mode, password: '' } : emptyForm();
    this.baseline = JSON.stringify(this.value); this.modalError.set(''); this.open.set(true); this.dialog?.nativeElement.showModal();
  }
  close() {
    if (!this.canLeave()) return;
    this.dialog?.nativeElement.close(); this.open.set(false); this.value.password = ''; this.editing = null;
  }
  onCancel(event: Event) { event.preventDefault(); this.close(); }
  valid() { return !!(this.value.name.trim() && this.value.host.trim() && this.value.database.trim() && this.value.username.trim()
    && (this.editing || this.value.password) && Number.isInteger(this.value.port) && this.value.port >= 1 && this.value.port <= 65535); }
  private replace(connection: DatabaseConnection) {
    this.connections.update(values => values.some(value => value.id === connection.id) ? values.map(value => value.id === connection.id ? connection : value) : [...values, connection]);
  }
  async save() {
    if (!this.valid() || this.busy() || !this.canManage()) return;
    this.busy.set('save'); this.modalError.set(''); this.error.set(''); this.info.set('');
    try {
      const result = this.editing ? await this.api.update(this.editing, this.value) : await this.api.create(this.value);
      this.replace(result); this.dialog?.nativeElement.close(); this.open.set(false); this.value.password = ''; this.editing = null;
      if (result.status === 'verified') this.info.set('Database connected and verified. It is ready for SQL Query actions.');
      else this.error.set(this.failure(result.last_error));
    } catch (error) { this.modalError.set(errorMessage(error)); }
    finally { this.busy.set(''); }
  }
  failure(code: string | null) {
    return code === 'credentials_unavailable' ? 'Saved credentials could not be opened. Edit this connection and enter its password again.'
      : 'Connection saved, but verification failed. Check the host, port, database, credentials, TLS settings, and network access, then verify again.';
  }
  async verify(connection: DatabaseConnection) {
    if (this.busy() || !this.canManage()) return;
    this.busy.set(connection.id); this.error.set(''); this.info.set('');
    try { const result = await this.api.verify(connection); this.replace(result);
      if (result.status === 'verified') this.info.set(`${result.name} verified successfully.`); else this.error.set(this.failure(result.last_error));
    } catch (error) { this.error.set(errorMessage(error)); }
    finally { this.busy.set(''); }
  }
  async remove(connection: DatabaseConnection) {
    if (this.busy() || !this.canManage() || !window.confirm(`Delete database connection “${connection.name}”?`)) return;
    this.busy.set(connection.id); this.error.set(''); this.info.set('');
    try { await this.api.remove(connection); this.connections.update(values => values.filter(value => value.id !== connection.id)); this.info.set('Database connection deleted.'); }
    catch (error) { this.error.set(errorMessage(error)); }
    finally { this.busy.set(''); }
  }
}
