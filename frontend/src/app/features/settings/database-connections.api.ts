import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../core/api.service';

export interface DatabaseConnection {
  id: string; name: string; type: 'postgresql'; host: string; port: number; database: string; username: string;
  ssl_mode: 'verify-full' | 'require' | 'disable'; status: 'unverified' | 'verified' | 'error'; last_error: string | null;
  revision: number; verified_at: string | null; created_at: string; updated_at: string;
}
export type ConnectionOption = Pick<DatabaseConnection, 'id' | 'name' | 'type'>;
export type ConnectionForm = Pick<DatabaseConnection, 'name' | 'type' | 'host' | 'port' | 'database' | 'username' | 'ssl_mode'> & { password: string };

@Injectable({ providedIn: 'root' })
export class DatabaseConnectionsApi {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(ApiService);
  private path() { return `/api/workspaces/${this.auth.context()!.workspace.id}/database-connections`; }
  list() { return firstValueFrom(this.http.get<{ connections: DatabaseConnection[] }>(this.path())); }
  options() { return firstValueFrom(this.http.get<{ connections: ConnectionOption[] }>(`${this.path()}/options`)); }
  create(body: ConnectionForm) { return firstValueFrom(this.http.post<DatabaseConnection>(this.path(), body)); }
  update(value: DatabaseConnection, form: ConnectionForm) {
    const { password, ...fields } = form;
    return firstValueFrom(this.http.post<DatabaseConnection>(`${this.path()}/${value.id}`, { ...fields,
      password: password || null, expected_revision: value.revision }));
  }
  verify(value: DatabaseConnection) { return firstValueFrom(this.http.post<DatabaseConnection>(`${this.path()}/${value.id}/verify`, { expected_revision: value.revision })); }
  remove(value: DatabaseConnection) { return firstValueFrom(this.http.post(`${this.path()}/${value.id}/delete`, { expected_revision: value.revision })); }
}
