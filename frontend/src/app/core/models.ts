export interface Workspace {
  id: string;
  name: string;
  role: 'owner' | 'admin' | 'member';
  industry: string | null;
  country_code: string | null;
  website: string | null;
  setup_completed: boolean;
  avatar_url: string | null;
  permissions: { manage_ai_agents: boolean; manage_workspace_roles: boolean };
  subscription: { plan_name: string; agent_limit: number; language_limit: number };
}

export interface AuthContext {
  user: { id: string; email: string; full_name: string | null };
  workspace: Workspace;
  next_path: string;
}

export interface AuthOptions {
  email: boolean;
  google: boolean;
  microsoft: boolean;
  magic_link_minutes: number;
}

export interface OnboardingOptions {
  countries: { code: string; name: string }[];
  industries: string[];
  default_country: string | null;
}

export interface Agent {
  id: string; workspace_id: string; name: string; avatar_url: string | null;
  llm_enabled: boolean; persona: string; languages: string[]; default_language: string;
  voice_enabled: boolean; voice_name: string; voice_speed: number;
  revision: number; created_at: string; updated_at: string;
}
export interface AgentOptions {
  languages: { code: string; name: string }[]; avatar_max_bytes: number;
  import_format: string; import_schema_version: number;
  tester_available: boolean; voice_runtime_available: boolean;
}
export interface AgentList {
  agents: Agent[]; can_manage: boolean;
  usage: { count: number; agent_limit: number; language_limit: number; plan_name: string };
}
export interface AgentVersion { number: number; section: string; actor: string; created_at: string }
export interface AgentChange { id: string; action: string; summary: string; revision: number | null; actor: string; created_at: string }
export interface AgentNetwork {
  nodes: { id: string; name: string; avatar_url: string | null }[];
  edges: { id: string; source_id: string; target_id: string }[];
}
export interface RoleOptions {
  roles: { role: string; manage_ai_agents: boolean; locked: boolean }[];
  subscription: Workspace['subscription'];
}
export interface TesterReply { conversation_id: string; message: { id: string; role: string; content: string; created_at: string } }
