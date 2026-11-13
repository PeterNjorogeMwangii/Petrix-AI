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
  paths?: BranchPath[];
}

export interface BranchPath { id: string; children: WorkflowNode[] }
export interface BranchCondition { id: string; name: string; chat_script: string }
export interface BranchConfig { conditions: BranchCondition[]; else_path_id: string }
export type APIMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
export interface APIHeader { key: string; value: string }
export interface SendAPIConfig {
  method: APIMethod; url: string; headers: APIHeader[]; payload_mode: 'json' | 'expression'; payload: string;
  variable_name: string; timeout_seconds: number; success_path_id: string; error_path_id: string; timeout_path_id: string;
}
export interface SQLQueryConfig {
  connection_id: string; query: string; query_mode: 'template' | 'expression'; timeout_seconds: number; variable_name: string;
  success_path_id: string; error_path_id: string; timeout_path_id: string;
}

export interface LLMProcessingConfig {
  input: string; input_mode: 'template' | 'expression'; model: 'intent'; temperature: number;
  system_prompt: string; system_prompt_mode: 'template' | 'expression'; use_knowledge_info: boolean;
  variable_name: string; timeout_seconds: number; success_path_id: string; error_path_id: string; timeout_path_id: string;
}
export interface LLMProcessingOptions { model: string; provider: string; available: boolean; knowledge_supported: boolean }

export interface WorkflowDocument {
  agent_id: string; revision: number; nodes: WorkflowNode[]; updated_at: string;
}
export interface TriggerDefinition {
  type: string; name: string; description: string; top_level_only: boolean; permanent: boolean;
}
export interface ActionDefinition {
  type: string; name: string; description: string; group: string; available: boolean;
  allowed_after_close?: boolean; post_close_only?: boolean;
}
export interface WorkflowOptions extends WorkflowDocument {
  triggers: TriggerDefinition[]; actions: ActionDefinition[]; llm_processing?: LLMProcessingOptions;
}
export interface MessageTranslation { messages: string[]; quick_replies: string[] }
export interface SendMessageConfig { translations: Record<string, MessageTranslation> }
export type AttachmentFormat = 'image' | 'audio' | 'video' | 'document';
export interface AttachmentTranslation {
  file_format: AttachmentFormat; source: 'upload' | 'link' | 'script'; asset_id: string | null; url: string; chat_script: string;
}
export interface SendAttachmentConfig { translations: Record<string, AttachmentTranslation> }
export interface CarouselImage { source: 'upload' | 'link'; asset_id: string | null; url: string }
export interface CarouselButton {
  id: string; label: string; type: 'reply' | 'link'; message: string; url: string; webview: boolean;
}
export interface CarouselCardConfig {
  id: string; title: string; subtitle: string; image: CarouselImage | null; buttons: CarouselButton[];
}
export interface ShowCarouselConfig { translations: Record<string, { cards: CarouselCardConfig[] }> }
export type FeedbackReplyType = 'open_response' | 'number' | 'email' | 'phone' | 'choice';
export interface FeedbackField { id: string; reply_type: FeedbackReplyType; required: boolean; variable_name: string }
export interface FeedbackFieldText { question: string; placeholder: string; choices: string[] }
export interface FeedbackTranslation {
  webchat_rating_title: string; rating_request_message: string; submission_text: string;
  fields: Record<string, FeedbackFieldText>;
}
export interface CollectFeedbackConfig {
  rating_enabled: boolean; rating_variable_name: string; survey_enabled: boolean; fields: FeedbackField[];
  translations: Record<string, FeedbackTranslation>;
}
export type FeedbackChannel = 'webchat' | 'messaging' | 'bitrix24' | 'africas_talking';
export interface FeedbackRequest {
  token: string; title: string; node_id: string; node_number: number; language: string; min_rating: number; max_rating: number;
  rating_enabled: boolean; request_message: string; rating_variable_name: string;
  channel: FeedbackChannel; typed_rating: boolean; rating_buttons: number[];
  fields: (FeedbackField & FeedbackFieldText)[];
}
export interface CarouselCard extends Omit<CarouselCardConfig, 'image'> { image: SurveyAttachment | null }
export interface WorkflowCarousel { cards: CarouselCard[] }
export interface WorkflowAssetMetadata {
  id: string; name: string; format: AttachmentFormat; content_type: string; size_bytes: number; url: string;
}
export interface SetVariableConfig { variable_name: string; chat_script: string }
export interface JumpConfig { target_node_id: string }
export interface SwitchLanguageConfig { target_language: string }
export type SurveyAnswerType = 'text' | 'number' | 'attachment' | 'attachments' | 'email' | 'phone';
export interface SurveyField {
  id: string; question: string; answer_type: SurveyAnswerType; invalid_answer_reply: string;
  variable_name: string; quick_answers: string[]; enable_skip: boolean;
}
export interface AskSurveyConfig { fields: SurveyField[] }
export interface SurveyProgress {
  node_id: string; node_number: number; field_id: string; question_number: number; total_fields: number;
  answer_type: SurveyAnswerType; variable_name: string; enable_skip: boolean; answer_token: string;
}
export interface SurveyAttachment {
  id: string; name: string; content_type: string; size_bytes: number | null; url: string;
  format?: AttachmentFormat; external?: boolean;
}
export interface WorkflowMessage {
  id: string; role: string; content: string; created_at: string;
  quick_replies?: string[]; source?: string; node_id?: string | null;
  survey?: SurveyProgress; attachments?: SurveyAttachment[]; carousel?: WorkflowCarousel;
}
export type WorkflowEventType = 'chat_started' | 'message_received' | 'attachment_received' | 'chat_closed' | 'collect_feedback' | 'chat_timeout' | 'ai_timeout';
export interface WorkflowEvent {
  event: WorkflowEventType; conversation_id?: string | null; language?: string;
  content?: string; attachment?: { name: string; media_type: string }; rating?: number;
  survey_answer_token?: string;
  feedback_token?: string;
  feedback_answers?: Record<string, string>;
  channel?: FeedbackChannel;
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
    target_node_id?: string; target_node_number?: number; field_id?: string; variable_name?: string;
    path_id?: string; condition_name?: string; status_code?: number | null; row_count?: number; error?: string | null; model?: string; knowledge_sources?: number;
    previous_language?: string; target_language?: string }[];
  workflow_revision: number; language: string; closed: boolean;
  feedback_submitted: boolean; waiting: boolean; notice: string;
  feedback?: FeedbackRequest | null; automation_error?: string | null; closed_at?: string | null; csat_rating?: number | null;
  feedback_confirmation?: string | null; channel?: FeedbackChannel;
  close_source?: 'tester' | 'workflow' | null;
  variables: Record<string, unknown>;
  matching?: IntentMatchResult;
  survey?: SurveyProgress | null; attachments?: SurveyAttachment[];
}
