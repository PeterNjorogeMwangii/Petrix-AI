import { NgTemplateOutlet } from '@angular/common';
import { Component, ElementRef, EventEmitter, Input, OnChanges, OnDestroy, Output, ViewChild, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Agent } from '../../core/models';
import { errorMessage } from '../../core/api.service';
import { IconComponent } from '../../shared/icon.component';
import { SendAttachmentEditorComponent, newAttachmentTranslation, sendAttachmentConfigError } from './actions/send-attachment/send-attachment-editor.component';
import { ShowCarouselEditorComponent } from './actions/show-carousel/show-carousel-editor.component';
import { carouselConfigError, newCarouselCard } from './actions/show-carousel/carousel-config';
import { CollectFeedbackEditorComponent, feedbackConfigError, newFeedbackConfig, normalizeFeedbackConfig } from './actions/collect-feedback/collect-feedback-editor.component';
import { PostCloseFlow, POST_CLOSE_ACTIONS } from './post-close';
import { SendMessageEditorComponent } from './actions/send-message/send-message-editor.component';
import { SetVariableEditorComponent, variableConfigError } from './actions/set-variable/set-variable-editor.component';
import { JumpEditorComponent } from './actions/jump/jump-editor.component';
import { jumpConfigError, workflowJumpTargets } from './actions/jump/jump-targets';
import { AskSurveyEditorComponent, newSurveyField, surveyConfigError } from './actions/ask-survey/ask-survey-editor.component';
import { BranchEditorComponent, branchConfigError, newBranchConfig } from './actions/branch/branch-editor.component';
import { SendAPIEditorComponent, newSendAPIConfig, sendAPIConfigError } from './actions/send-api/send-api-editor.component';
import { SQLQueryEditorComponent, newSQLQueryConfig, sqlQueryConfigError } from './actions/sql-query/sql-query-editor.component';
import { LLMProcessingEditorComponent, newLLMProcessingConfig, llmProcessingConfigError } from './actions/llm-processing/llm-processing-editor.component';
import { SwitchLanguageEditorComponent, switchLanguageConfigError } from './actions/switch-language/switch-language-editor.component';
import { actionPaths, childLists, containerEnabled, findWorkflowNode, visitWorkflow, workflowList, uploadedAssetIds } from './workflow-tree';
import { LLMProcessingConfig, LLMProcessingOptions, ActionDefinition, AskSurveyConfig, BranchConfig, JumpConfig, SendAPIConfig, SQLQueryConfig, SendAttachmentConfig, ShowCarouselConfig, CollectFeedbackConfig, SendMessageConfig, SetVariableConfig, SwitchLanguageConfig, TriggerDefinition, WorkflowDocument, WorkflowNode } from './workflow.models';
import { WorkflowsApi } from './workflows.api';

interface Insertion { parentId: string | null; beforeId: string | null }

@Component({
  selector: 'petrix-workflows',
  imports: [FormsModule, NgTemplateOutlet, IconComponent, SendMessageEditorComponent, SendAttachmentEditorComponent, ShowCarouselEditorComponent, CollectFeedbackEditorComponent, SetVariableEditorComponent, JumpEditorComponent, AskSurveyEditorComponent, BranchEditorComponent, SendAPIEditorComponent, SQLQueryEditorComponent, LLMProcessingEditorComponent, SwitchLanguageEditorComponent],
  templateUrl: './workflows.component.html', styleUrl: './workflows.component.css'
})
export class WorkflowsComponent implements OnChanges, OnDestroy {
  @Input({ required: true }) agent!: Agent;
  @Input() canManage = false;
  @Input() languages: { code: string; name: string }[] = [];
  @Output() busyChange = new EventEmitter<boolean>();
  @Output() saved = new EventEmitter<number>();
  @ViewChild('workflowDialog') dialog?: ElementRef<HTMLDialogElement>;
  private readonly api = inject(WorkflowsApi);
  readonly document = signal<WorkflowDocument | null>(null);
  readonly loading = signal(false);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly success = signal('');
  readonly rootId = signal<string | null>(null);
  readonly editor = signal<WorkflowNode | null>(null);
  readonly dialogType = signal<'trigger' | 'action' | null>(null);
  triggers: TriggerDefinition[] = [];
  actions: ActionDefinition[] = [];
  groups = ['Messaging', 'Handoffs', 'Flow control', 'AI & data'];
  editLanguage = 'en';
  editFeedback: CollectFeedbackConfig = newFeedbackConfig('en');
  editCarousel: ShowCarouselConfig = { translations: {} };
  editAttachment: SendAttachmentConfig = { translations: {} };
  private assetCleanup = new Map<string, string>();
  editMessages: SendMessageConfig = { translations: {} };
  editVariable: SetVariableConfig = { variable_name: '', chat_script: '' };
  editJump: JumpConfig = { target_node_id: '' };
  editSurvey: AskSurveyConfig = { fields: [] };
  editBranch: BranchConfig = { conditions: [], else_path_id: '' };
  editAPI: SendAPIConfig = newSendAPIConfig();
  editSQL: SQLQueryConfig = newSQLQueryConfig();
  editLLM: LLMProcessingConfig = newLLMProcessingConfig();
  llmOptions: LLMProcessingOptions | null = null;
  editSwitchLanguage: SwitchLanguageConfig = { target_language: '' };
  readonly actionPaths = actionPaths;
  examplesText = '';
  matchMode = 'approximate'; threshold = 0.65;
  attachmentTypes: string[] = [];
  createType = 'message_received'; createName = ''; createDescription = '';
  private insertion: Insertion | null = null;
  private editorInsertion: Insertion | null = null;
  private baseline = '';
  private loadedAgent = '';
  private generation = 0;

  ngOnChanges() {
    if (this.agent.id !== this.loadedAgent) {
      this.loadedAgent = this.agent.id; this.rootId.set(null); this.clearEditor(); void this.load();
    }
  }
  ngOnDestroy() { this.cleanupAssets(); ++this.generation; this.dialog?.nativeElement.close(); }
  async load() {
    if (!this.canDiscard()) return;
    const generation = ++this.generation; this.loading.set(true); this.error.set('');
    try {
      const data = await this.api.load(this.agent.id);
      if (generation !== this.generation) return;
      this.document.set(data); this.triggers = data.triggers; this.actions = data.actions; this.llmOptions = data.llm_processing || null;
      this.clearEditor(); this.saved.emit(data.revision);
      if (this.rootId() && !this.root()) this.rootId.set(null);
    } catch (error) { if (generation === this.generation) this.error.set(errorMessage(error)); }
    finally { if (generation === this.generation) this.loading.set(false); }
  }
  root() { return this.document()?.nodes.find(node => node.id === this.rootId()) || null; }
  definition(type: string, kind?: 'trigger' | 'action') { return (kind === 'action' ? this.actions : kind === 'trigger' ? this.triggers : [...this.triggers, ...this.actions]).find(value => value.type === type); }
  label(type: string, kind?: 'trigger' | 'action') { return this.definition(type,kind)?.name || type; }
  actionIcon(type: string) { return type === 'close_chat' ? 'chat_end' : type === 'set_variable' ? 'variable' : type === 'collect_feedback' ? 'star' : type === 'show_carousel' ? 'cards' : type === 'send_attachment' ? 'paperclip' : type === 'send_message' ? 'chat' : type === 'jump' ? 'jump' : type === 'ask_survey' ? 'survey' : type === 'branch' ? 'nodes' : type === 'send_api' ? 'globe' : type === 'llm_processing' ? 'spark' : type === 'sql_query' ? 'database' : type === 'switch_language' ? 'language' : 'bolt'; }
  icon(node: WorkflowNode) { return node.kind === 'action' ? this.actionIcon(node.type) : node.type === 'attachment_received' ? 'upload' : node.type === 'message_received' ? 'chat' : node.type === 'fallback' ? 'help' : 'spark'; }
  permanent(node: WorkflowNode) { return this.triggers.some(value => value.type === node.type && value.permanent); }
  languageName(code: string) { return this.languages.find(value => value.code === code)?.name || code; }
  availableTriggers() { return this.triggers.filter(value => !value.permanent); }
  postCloseMode() { return this.root()?.type === 'chat_closed'; }
  visibleGroups() { return this.groups.filter(group => this.groupedActions(group).length); }
  groupedActions(group: string) { return this.actions.filter(value => value.group === group && (this.postCloseMode() ? value.allowed_after_close ?? POST_CLOSE_ACTIONS.includes(value.type) : !value.post_close_only)); }
  jumpTargets() {
    const nodes = this.document()?.nodes || []; const flow = new PostCloseFlow(nodes);
    return workflowJumpTargets(nodes).filter(target => target.id !== this.editor()?.id).map(target => ({ ...target,
      unavailableReason: this.postCloseMode() ? flow.issue(target.id) : flow.owners.get(target.id) === 'chat_closed' ? 'Only Chat Closed automation can enter these steps.' : '' }));
  }
  jumpActive() {
    const node = this.editor(); if (!node?.enabled) return false;
    const targets = workflowJumpTargets(this.document()?.nodes || []);
    return this.editorInsertion
      ? containerEnabled(this.document()?.nodes || [], this.editorInsertion.parentId) ?? true
      : targets.find(target => target.id === node.id)?.ancestorsEnabled ?? true;
  }
  jumpError() { return jumpConfigError(this.editJump, this.jumpTargets(), this.jumpActive()); }
  feedbackError() { return feedbackConfigError(this.editFeedback); }
  updateFeedback(config: CollectFeedbackConfig) { this.editFeedback = config; this.error.set(''); }
  updateCarousel(config: ShowCarouselConfig) { this.editCarousel = config; this.error.set(''); }
  carouselError() { return carouselConfigError(this.editCarousel); }
  updateAttachment(config: SendAttachmentConfig) { this.editAttachment = config; this.error.set(''); }
  attachmentError() { return sendAttachmentConfigError(this.editAttachment); }
  onAttachmentBusy(value: boolean) { this.busy.set(value); this.busyChange.emit(value); }
  trackAsset(id: string) { this.assetCleanup.set(id, this.agent.id); }
  private cleanupAssets() {
    const files = [...this.assetCleanup]; this.assetCleanup.clear();
    const referenced = new Set<string>();
    visitWorkflow(this.document()?.nodes || [], node => {
      uploadedAssetIds(node).forEach(id => referenced.add(id));
    });
    for (const [id, agentId] of files) {
      if (agentId === this.document()?.agent_id && referenced.has(id)) continue;
      void this.api.deleteAsset(agentId, id).catch(() => {});
    }
  }
  surveyError() { return surveyConfigError(this.editSurvey); }
  branchError() { return branchConfigError(this.editBranch); }
  apiError() { return sendAPIConfigError(this.editAPI); }
  llmError() { return llmProcessingConfigError(this.editLLM); }
  sqlError() { return sqlQueryConfigError(this.editSQL); }
  switchLanguageError() { return switchLanguageConfigError(this.editSwitchLanguage, this.agent.languages); }
  configuredLanguageOptions() { return this.agent.languages.map(code => ({ code, name: this.languageName(code) })); }
  jumpTargetLabel(node: WorkflowNode) {
    const target = this.find(node.config['target_node_id']);
    return target ? `Jump to #${target.number} ${target.name}` : 'Choose a target';
  }
  private find(id: string, nodes = this.document()?.nodes || []): WorkflowNode | null {
    return findWorkflowNode(nodes, id);
  }
  private listFor(nodes: WorkflowNode[], parentId: string | null): WorkflowNode[] {
    return workflowList(nodes, parentId) || [];
  }
  private editingValue() {
    const node = this.editor(); if (!node) return null;
    const result = structuredClone(node);
    if (result.kind === 'action' && result.type === 'collect_feedback') result.config = structuredClone(this.editFeedback);
    if (result.type === 'show_carousel') result.config = structuredClone(this.editCarousel);
    if (result.type === 'send_attachment') result.config = structuredClone(this.editAttachment);
    if (result.type === 'send_message') result.config = structuredClone(this.editMessages);
    if (result.type === 'set_variable') result.config = structuredClone(this.editVariable);
    if (result.type === 'jump') result.config = structuredClone(this.editJump);
    if (result.type === 'switch_language') result.config = structuredClone(this.editSwitchLanguage);
    if (result.type === 'ask_survey') result.config = structuredClone(this.editSurvey);
    if (result.type === 'branch') {
      result.config = structuredClone(this.editBranch);
      result.paths = [...this.editBranch.conditions.map(condition => condition.id), this.editBranch.else_path_id]
        .map(id => ({ id, children: result.paths?.find(path => path.id === id)?.children || [] }));
    }
    if (result.type === 'send_api') {
      result.config = structuredClone(this.editAPI);
      result.paths = [this.editAPI.success_path_id, this.editAPI.error_path_id, this.editAPI.timeout_path_id]
        .map(id => ({ id, children: result.paths?.find(path => path.id === id)?.children || [] }));
    }
    if (result.type === 'sql_query') {
      result.config = structuredClone(this.editSQL);
      result.paths = [this.editSQL.success_path_id, this.editSQL.error_path_id, this.editSQL.timeout_path_id]
        .map(id => ({ id, children: result.paths?.find(path => path.id === id)?.children || [] }));
    }
    if (result.type === 'llm_processing') {
      result.config = structuredClone(this.editLLM);
      result.paths = [this.editLLM.success_path_id, this.editLLM.error_path_id, this.editLLM.timeout_path_id]
        .map(id => ({ id, children: result.paths?.find(path => path.id === id)?.children || [] }));
    }
    if (result.type === 'message_received') result.config = { ...result.config, match_mode: this.matchMode, threshold: Number(this.threshold) };
    if (result.type === 'attachment_received') result.config = { attachment_types: [...this.attachmentTypes] };
    return result;
  }
  dirty() { return !!this.editor() && (!!this.editorInsertion || JSON.stringify(this.editingValue()) !== this.baseline); }
  canDiscard() {
    if (this.busy()) return false;
    if (this.dirty() && !window.confirm('Discard the unsaved step settings?')) return false;
    this.clearEditor(); return true;
  }
  private clearEditor() { this.cleanupAssets(); this.editor.set(null); this.editorInsertion = null; this.baseline = ''; }
  openRoot(node: WorkflowNode) { if (this.canDiscard()) { this.rootId.set(node.id); this.error.set(''); this.success.set(''); } }
  back() { if (this.canDiscard()) { this.rootId.set(null); this.success.set(''); } }
  closeEditor() { this.canDiscard(); }
  edit(node: WorkflowNode, insertion: Insertion | null = null) {
    if (!this.canDiscard()) return;
    this.setEditor(node, insertion);
  }
  private setEditor(node: WorkflowNode, insertion: Insertion | null) {
    this.editorInsertion = insertion; this.editLanguage = this.agent.default_language;
    this.editor.set(structuredClone(node));
    this.editFeedback = node.kind === 'action' && node.type === 'collect_feedback' ? normalizeFeedbackConfig(node.config as unknown as CollectFeedbackConfig) : newFeedbackConfig(this.agent.default_language);
    this.editAttachment = node.type === 'send_attachment' ? structuredClone(node.config as unknown as SendAttachmentConfig) : { translations: {} };
    this.editCarousel = node.type === 'show_carousel' ? structuredClone(node.config as unknown as ShowCarouselConfig) : { translations: {} };
    uploadedAssetIds(node).forEach(id => this.trackAsset(id));
    this.editMessages = structuredClone(node.config['translations'] ? node.config as SendMessageConfig : { translations: {} });
    this.editVariable = node.type === 'set_variable' ? structuredClone(node.config as unknown as SetVariableConfig) : { variable_name: '', chat_script: '' };
    this.editJump = node.type === 'jump' ? { target_node_id: node.config['target_node_id'] || '' } : { target_node_id: '' };
    this.editSurvey = node.type === 'ask_survey' ? structuredClone(node.config as unknown as AskSurveyConfig) : { fields: [] };
    this.editBranch = node.type === 'branch' ? structuredClone(node.config as unknown as BranchConfig) : { conditions: [], else_path_id: '' };
    this.editAPI = node.type === 'send_api' ? structuredClone(node.config as unknown as SendAPIConfig) : newSendAPIConfig();
    this.editSQL = node.type === 'sql_query' ? structuredClone(node.config as unknown as SQLQueryConfig) : newSQLQueryConfig();
    this.editLLM = node.type === 'llm_processing' ? { ...newLLMProcessingConfig(), ...structuredClone(node.config as unknown as LLMProcessingConfig) } : newLLMProcessingConfig();
    this.editSwitchLanguage = node.type === 'switch_language' ? structuredClone(node.config as unknown as SwitchLanguageConfig) : { target_language: this.agent.default_language };
    this.matchMode = node.config['match_mode'] || 'approximate'; this.threshold = node.config['threshold'] ?? 0.65;
    this.attachmentTypes = [...(node.config['attachment_types'] || ['image', 'audio', 'video', 'file'])];
    this.examplesText = (node.config['examples']?.[this.editLanguage] || []).join('\n');
    this.baseline = JSON.stringify(this.editingValue());
    this.error.set(''); this.success.set('');
  }
  setEditLanguage(code: string) { this.editLanguage = code; this.examplesText = (this.editor()?.config['examples']?.[code] || []).join('\n'); }
  updateExamples() {
    const node = this.editor(); if (!node) return;
    node.config = { ...node.config, examples: { ...node.config['examples'], [this.editLanguage]: this.examplesText.split('\n').map(value => value.trim()).filter(Boolean) } };
  }
  toggleAttachment(type: string) { this.attachmentTypes = this.attachmentTypes.includes(type) ? this.attachmentTypes.filter(value => value !== type) : [...this.attachmentTypes, type]; }
  async saveEditor() {
    const value = this.editingValue(); if (!value || !this.canManage || this.busy()) return;
    if (value.kind === 'action' && value.type === 'collect_feedback' && this.feedbackError()) { this.error.set(this.feedbackError()); return; }
    if (value.type === 'show_carousel' && this.carouselError()) { this.error.set(this.carouselError()); return; }
    if (value.type === 'send_attachment' && this.attachmentError()) { this.error.set(this.attachmentError()); return; }
    if (value.type === 'jump' && this.jumpError()) { this.error.set(this.jumpError()); return; }
    if (value.type === 'ask_survey' && this.surveyError()) { this.error.set(this.surveyError()); return; }
    if (value.type === 'branch' && this.branchError()) { this.error.set(this.branchError()); return; }
    if (value.type === 'send_api' && this.apiError()) { this.error.set(this.apiError()); return; }
    if (value.type === 'llm_processing' && this.llmError()) { this.error.set(this.llmError()); return; }
    if (value.type === 'sql_query' && this.sqlError()) { this.error.set(this.sqlError()); return; }
    if (value.type === 'switch_language' && this.switchLanguageError()) { this.error.set(this.switchLanguageError()); return; }
    if (value.type === 'set_variable') {
      const error = variableConfigError(this.editVariable);
      if (error) { this.error.set(error); return; }
    }
    if (value.type === 'send_message') {
      const translations = this.editMessages.translations;
      if (!Object.keys(translations).length || Object.values(translations).some(item => item.messages.some(text => !text.trim()) || item.quick_replies.some(text => !text.trim()))) {
        this.error.set('Fill in every message and quick reply, or remove the empty entries.'); return;
      }
    }
    const nodes = structuredClone(this.document()!.nodes);
    if (this.editorInsertion) {
      const list = this.listFor(nodes, this.editorInsertion.parentId);
      let index = this.editorInsertion.beforeId ? list.findIndex(node => node.id === this.editorInsertion!.beforeId) : list.findIndex(node => node.kind === 'trigger');
      if (index < 0) index = list.length;
      list.splice(index, 0, value);
    } else {
      const replace = (values: WorkflowNode[]) => {
        const index = values.findIndex(node => node.id === value.id);
        if (index >= 0) values[index] = value; else values.forEach(node => childLists(node).forEach(list => replace(list)));
      };
      replace(nodes);
    }
    if (await this.persist(nodes)) { this.clearEditor(); this.success.set('Step saved. Restart the tester to replay Chat started.'); }
  }
  private async persist(nodes: WorkflowNode[]) {
    const document = this.document(); if (!document || !this.canManage || this.busy()) return false;
    this.busy.set(true); this.busyChange.emit(true); this.error.set(''); this.success.set('');
    try {
      const result = await this.api.save(this.agent.id, document.revision, nodes);
      this.document.set(result); this.saved.emit(result.revision); return true;
    } catch (error) { this.error.set(errorMessage(error)); return false; }
    finally { this.busy.set(false); this.busyChange.emit(false); }
  }
  newTrigger(parentId: string | null = null) {
    if ((parentId && this.postCloseMode()) || !this.canManage || !this.canDiscard()) return;
    this.insertion = { parentId, beforeId: null }; this.createType = 'message_received'; this.createName = ''; this.createDescription = '';
    this.showDialog('trigger');
  }
  addAction(parentId: string, beforeId: string | null = null) {
    if (!this.canManage || !this.canDiscard()) return;
    this.insertion = { parentId, beforeId }; this.showDialog('action');
  }
  private showDialog(type: 'trigger' | 'action') { this.dialogType.set(type); this.error.set(''); this.dialog?.nativeElement.showModal(); }
  closeDialog() { if (this.busy()) return; this.dialogType.set(null); this.dialog?.nativeElement.close(); }
  onCancel(event: Event) { event.preventDefault(); this.closeDialog(); }
  hasFallbackAtInsertion() { return this.document() ? this.listFor(this.document()!.nodes, this.insertion?.parentId || null).some(node => node.type === 'fallback') : false; }
  async createTrigger() {
    if (!this.createName.trim() || !this.insertion) return;
    const config = this.createType === 'message_received' ? { examples: {}, match_mode: 'approximate', threshold: 0.65 } : this.createType === 'attachment_received' ? { attachment_types: ['image', 'audio', 'video', 'file'] } : {};
    const node: WorkflowNode = { id: crypto.randomUUID(), kind: 'trigger', type: this.createType, name: this.createName.trim(), description: this.createDescription.trim(), enabled: this.createType !== 'message_received', config, children: [] };
    const nodes = structuredClone(this.document()!.nodes); this.listFor(nodes, this.insertion.parentId).push(node);
    const root = !this.insertion.parentId;
    if (await this.persist(nodes)) {
      this.closeDialog(); if (root) this.rootId.set(node.id);
      const savedNode = this.find(node.id);
      if (savedNode) this.setEditor(savedNode, null);
    }
  }
  chooseAction(action: ActionDefinition) {
    if (!action.available || !this.insertion || (this.postCloseMode() && !POST_CLOSE_ACTIONS.includes(action.type))) return;
    const config = action.type === 'send_message' ? { translations: { [this.agent.default_language]: { messages: [''], quick_replies: [] } } }
      : action.type === 'collect_feedback' ? newFeedbackConfig(this.agent.default_language)
      : action.type === 'show_carousel' ? { translations: { [this.agent.default_language]: { cards: [newCarouselCard()] } } }
      : action.type === 'send_attachment' ? { translations: { [this.agent.default_language]: newAttachmentTranslation() } }
      : action.type === 'set_variable' ? { variable_name: '', chat_script: '' }
      : action.type === 'jump' ? { target_node_id: '' }
      : action.type === 'switch_language' ? { target_language: this.agent.default_language }
      : action.type === 'ask_survey' ? { fields: [newSurveyField()] }
      : action.type === 'branch' ? newBranchConfig()
      : action.type === 'send_api' ? newSendAPIConfig()
      : action.type === 'sql_query' ? newSQLQueryConfig()
      : action.type === 'llm_processing' ? newLLMProcessingConfig()
      : action.type === 'close_chat' ? {} : null;
    if (!config) return;
    const insertion = { ...this.insertion }; this.closeDialog();
    const node: WorkflowNode = { id: crypto.randomUUID(), kind: 'action', type: action.type, name: action.name, description: '', enabled: true,
      config, children: [] };
    this.setEditor(node, insertion);
  }
  async toggle(node: WorkflowNode) {
    if (this.permanent(node) || !this.canManage || !this.canDiscard()) return;
    const nodes = structuredClone(this.document()!.nodes);
    visitWorkflow(nodes, value => { if (value.id === node.id) value.enabled = !value.enabled; });
    await this.persist(nodes);
  }
  async remove(node: WorkflowNode, parentId: string | null) {
    if (this.permanent(node) || !this.canManage || !this.canDiscard()) return;
    if (!window.confirm(`Delete “${node.name}” and all its nested steps?`)) return;
    const nodes = structuredClone(this.document()!.nodes); const list = this.listFor(nodes, parentId);
    list.splice(list.findIndex(value => value.id === node.id), 1);
    if (await this.persist(nodes)) {
      visitWorkflow([node], value => uploadedAssetIds(value).forEach(id => this.trackAsset(id)));
      this.cleanupAssets(); if (node.id === this.rootId()) this.rootId.set(null); this.success.set('Step deleted.'); }
  }
  canMove(siblings: WorkflowNode[], index: number, direction: number) { return !!siblings[index + direction] && siblings[index + direction].kind === siblings[index].kind; }
  async move(node: WorkflowNode, parentId: string, direction: number) {
    if (!this.canManage || !this.canDiscard()) return;
    const nodes = structuredClone(this.document()!.nodes); const list = this.listFor(nodes, parentId); const index = list.findIndex(value => value.id === node.id);
    if (!this.canMove(list, index, direction)) return;
    [list[index], list[index + direction]] = [list[index + direction], list[index]]; await this.persist(nodes);
  }
  firstTrigger(nodes: WorkflowNode[]) { return nodes.find(node => node.kind === 'trigger')?.id || null; }
}
