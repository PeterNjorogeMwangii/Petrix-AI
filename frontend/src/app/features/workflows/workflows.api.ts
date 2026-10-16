import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../core/api.service';
import { WorkflowDocument, WorkflowEvent, WorkflowNode, WorkflowOptions, WorkflowTestResult } from './workflow.models';

@Injectable({ providedIn: 'root' })
export class WorkflowsApi {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(ApiService);
  private path(agentId: string) { return `/api/workspaces/${this.auth.context()!.workspace.id}/agents/${agentId}`; }
  load(id: string) { return firstValueFrom(this.http.get<WorkflowOptions>(`${this.path(id)}/workflows`)); }
  save(id: string, expected_revision: number, nodes: WorkflowNode[]) {
    return firstValueFrom(this.http.post<WorkflowDocument>(`${this.path(id)}/workflows`, { expected_revision, nodes }));
  }
  event(id: string, body: WorkflowEvent) {
    return firstValueFrom(this.http.post<WorkflowTestResult>(`${this.path(id)}/tester/events`, body));
  }
  uploadSurveyAnswer(id: string, conversationId: string, answerToken: string, files: File[], language: string) {
    const body = new FormData();
    body.append('conversation_id', conversationId); body.append('survey_answer_token', answerToken); body.append('language', language);
    files.forEach(file => body.append('files', file));
    return firstValueFrom(this.http.post<WorkflowTestResult>(`${this.path(id)}/tester/attachments`, body));
  }
}
