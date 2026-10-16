import { Injectable, inject, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { Agent, AgentChange, AgentList, AgentNetwork, AgentOptions, AgentVersion, AuthContext, AuthOptions, OnboardingOptions, RoleOptions, TesterReply, Workspace } from './models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  readonly context = signal<AuthContext | null>(null);
  readonly magicLinkMinutes = signal(15);
  emailForInbox = '';

  authOptions() { return firstValueFrom(this.http.get<AuthOptions>('/api/auth/options')); }
  async requestLink(email: string) {
    const result = await firstValueFrom(this.http.post<{ message: string; expires_in_minutes: number }>('/api/auth/magic-link', { email }));
    this.magicLinkMinutes.set(result.expires_in_minutes);
    return result;
  }
  async verify(token: string) {
    const result = await firstValueFrom(this.http.post<AuthContext>('/api/auth/verify', { token }));
    this.context.set(result);
    return result;
  }
  async loadContext() {
    const result = await firstValueFrom(this.http.get<AuthContext>('/api/auth/me'));
    this.context.set(result);
    return result;
  }
  async logout() {
    await firstValueFrom(this.http.post('/api/auth/logout', {}));
    this.context.set(null);
  }
  onboardingOptions() { return firstValueFrom(this.http.get<OnboardingOptions>('/api/onboarding/options')); }
  async setupWorkspace(id: string, body: FormData) {
    const result = await firstValueFrom(this.http.post<{ workspace: Workspace; next_path: string }>(`/api/workspaces/${id}/setup`, body));
    this.context.update(context => context ? { ...context, workspace: result.workspace, next_path: result.next_path } : null);
    return result;
  }
  providerInfo(ticket: string) {
    return firstValueFrom(this.http.post<{ provider: string; email_hint: string | null }>('/api/auth/provider-email/info', { token: ticket }));
  }
  async providerEmail(ticket: string, email: string) {
    const result = await firstValueFrom(this.http.post<{ expires_in_minutes: number }>('/api/auth/provider-email', { ticket, email }));
    this.magicLinkMinutes.set(result.expires_in_minutes);
    return result;
  }
  agentOptions() { return firstValueFrom(this.http.get<AgentOptions>('/api/agents/options')); }
  private agentsPath() { return `/api/workspaces/${this.context()!.workspace.id}/agents`; }
  agents() { return firstValueFrom(this.http.get<AgentList>(this.agentsPath())); }
  createAgent(body: FormData) { return firstValueFrom(this.http.post<Agent>(this.agentsPath(), body)); }
  importAgent(file: File) { const body = new FormData(); body.append('file', file); return firstValueFrom(this.http.post<Agent>(`${this.agentsPath()}/import`, body)); }
  saveAgent(id: string, section: string, body: object | FormData) { return firstValueFrom(this.http.post<Agent>(`${this.agentsPath()}/${id}/${section}`, body)); }
  deleteAgent(agent: Agent, confirmation_name: string) { return firstValueFrom(this.http.post(`${this.agentsPath()}/${agent.id}/delete`, { expected_revision: agent.revision, confirmation_name })); }
  exportAgent(id: string) { return firstValueFrom(this.http.get(`${this.agentsPath()}/${id}/export`, { responseType: 'blob' })); }
  versions(id: string, offset = 0) { return firstValueFrom(this.http.get<{ versions: AgentVersion[]; has_more: boolean }>(`${this.agentsPath()}/${id}/versions?offset=${offset}`)); }
  version(id: string, number: number) { return firstValueFrom(this.http.get<{ number: number; configuration: Omit<Agent, 'id' | 'workspace_id' | 'avatar_url' | 'revision' | 'created_at' | 'updated_at'>; has_avatar: boolean }>(`${this.agentsPath()}/${id}/versions/${number}`)); }
  restoreVersion(agent: Agent, number: number) { return firstValueFrom(this.http.post<Agent>(`${this.agentsPath()}/${agent.id}/versions/${number}/restore`, { expected_revision: agent.revision })); }
  changes(id: string, offset = 0) { return firstValueFrom(this.http.get<{ changes: AgentChange[]; has_more: boolean }>(`${this.agentsPath()}/${id}/changes?offset=${offset}`)); }
  network(id: string) { return firstValueFrom(this.http.get<AgentNetwork>(`${this.agentsPath()}/${id}/network`)); }
  connectAgent(id: string, target_id: string) { return firstValueFrom(this.http.post(`${this.agentsPath()}/${id}/network`, { target_id })); }
  disconnectAgent(id: string, edge: string) { return firstValueFrom(this.http.post(`${this.agentsPath()}/${id}/network/${edge}/delete`, {})); }
  roles() { return firstValueFrom(this.http.get<RoleOptions>(`/api/workspaces/${this.context()!.workspace.id}/roles`)); }
  saveRoles(body: { admin: boolean; member: boolean }) { return firstValueFrom(this.http.post(`/api/workspaces/${this.context()!.workspace.id}/roles`, body)); }
  testerMessage(id: string, content: string, conversation_id: string | null) { return firstValueFrom(this.http.post<TesterReply>(`${this.agentsPath()}/${id}/tester/messages`, { content, conversation_id })); }
}

export function errorMessage(error: unknown, fallback = 'Something went wrong. Please try again.'): string {
  if (error instanceof HttpErrorResponse) {
    if (error.status === 0) return 'We cannot reach Petrix AI right now. Check your connection and try again.';
    const detail = error.error?.detail;
    if (typeof detail === 'string') return detail;
    if (error.status === 422) return 'Check the information you entered and try again.';
    if (error.status >= 500) return 'Petrix AI is temporarily unavailable. Please try again shortly.';
  }
  return fallback;
}
