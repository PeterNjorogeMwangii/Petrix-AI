export interface WorkflowNode {
  id: string;
  /** Assigned by the server on first save; absent only for an unsaved draft. */
  number?: number;
  kind: 'trigger' | 'action';
  type: string;
  name: string;
  description: string;
  enabled: boolean;
  config: Record<string, any>;
  children: WorkflowNode[];
}

export interface WorkflowDocument {
  agent_id: string; revision: number; nodes: WorkflowNode[]; updated_at: string;
}
export interface TriggerDefinition {
  type: string; name: string; description: string; top_level_only: boolean; permanent: boolean;
}
export interface ActionDefinition {
  type: string; name: string; description: string; group: string; available: boolean;
}
export interface WorkflowOptions extends WorkflowDocument {
  triggers: TriggerDefinition[]; actions: ActionDefinition[];
}
export interface MessageTranslation { messages: string[]; quick_replies: string[] }
export interface SendMessageConfig { translations: Record<string, MessageTranslation> }
export interface SetVariableConfig { variable_name: string; chat_script: string }
export interface JumpConfig { target_node_id: string }
export interface WorkflowMessage {
  id: string; role: string; content: string; created_at: string;
  quick_replies?: string[]; source?: string; node_id?: string | null;
}
export type WorkflowEventType = 'chat_started' | 'message_received' | 'attachment_received' | 'chat_closed' | 'collect_feedback' | 'chat_timeout' | 'ai_timeout';
export interface WorkflowEvent {
  event: WorkflowEventType; conversation_id?: string | null; language?: string;
  content?: string; attachment?: { name: string; media_type: string }; rating?: number;
}
export interface IntentMatchResult {
  method: string; scope: 'root' | 'nested'; node_id: string | null; node_number: number | null;
  intent_name: string | null; score: number | null; entities: Record<string, string>;
  needs_clarification: boolean; clarification_question: string | null; context_summary: string | null;
  attempts: { stage: string; status: string; scope: string; score: number | null }[];
  warnings: string[];
}
export interface WorkflowTestResult {
  conversation_id: string; messages: WorkflowMessage[];
  trace: { node_id: string | null; node_number?: number | null; name: string; type: string; outcome: string;
    target_node_id?: string; target_node_number?: number }[];
  workflow_revision: number; language: string; closed: boolean;
  feedback_submitted: boolean; waiting: boolean; notice: string;
  variables: Record<string, unknown>;
  matching?: IntentMatchResult;
}
