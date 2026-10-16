import { NgTemplateOutlet } from '@angular/common';
import { Component, ElementRef, EventEmitter, Input, OnChanges, OnDestroy, Output, ViewChild, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Agent } from '../../core/models';
import { errorMessage } from '../../core/api.service';
import { IconComponent } from '../../shared/icon.component';
import { SendMessageEditorComponent } from './actions/send-message/send-message-editor.component';
import { SetVariableEditorComponent, variableConfigError } from './actions/set-variable/set-variable-editor.component';
import { JumpEditorComponent } from './actions/jump/jump-editor.component';
import { jumpConfigError, workflowJumpTargets } from './actions/jump/jump-targets';
import { AskSurveyEditorComponent, newSurveyField, surveyConfigError } from './actions/ask-survey/ask-survey-editor.component';
import { ActionDefinition, AskSurveyConfig, JumpConfig, SendMessageConfig, SetVariableConfig, TriggerDefinition, WorkflowDocument, WorkflowNode } from './workflow.models';
import { WorkflowsApi } from './workflows.api';

interface Insertion { parentId: string | null; beforeId: string | null }

@Component({
  selector: 'petrix-workflows',
  imports: [FormsModule, NgTemplateOutlet, IconComponent, SendMessageEditorComponent, SetVariableEditorComponent, JumpEditorComponent, AskSurveyEditorComponent],
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
  editMessages: SendMessageConfig = { translations: {} };
  editVariable: SetVariableConfig = { variable_name: '', chat_script: '' };
  editJump: JumpConfig = { target_node_id: '' };
  editSurvey: AskSurveyConfig = { fields: [] };
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
  ngOnDestroy() { ++this.generation; this.dialog?.nativeElement.close(); }
  async load() {
    if (!this.canDiscard()) return;
    const generation = ++this.generation; this.loading.set(true); this.error.set('');
    try {
      const data = await this.api.load(this.agent.id);
      if (generation !== this.generation) return;
      this.document.set(data); this.triggers = data.triggers; this.actions = data.actions;
      this.clearEditor(); this.saved.emit(data.revision);
      if (this.rootId() && !this.root()) this.rootId.set(null);
    } catch (error) { if (generation === this.generation) this.error.set(errorMessage(error)); }
    finally { if (generation === this.generation) this.loading.set(false); }
  }
  root() { return this.document()?.nodes.find(node => node.id === this.rootId()) || null; }
  definition(type: string) { return [...this.triggers, ...this.actions].find(value => value.type === type); }
  label(type: string) { return this.definition(type)?.name || type; }
  actionIcon(type: string) { return type === 'set_variable' ? 'variable' : type === 'send_message' ? 'chat' : type === 'jump' ? 'jump' : type === 'ask_survey' ? 'survey' : 'bolt'; }
  icon(node: WorkflowNode) { return node.kind === 'action' ? this.actionIcon(node.type) : node.type === 'attachment_received' ? 'upload' : node.type === 'message_received' ? 'chat' : node.type === 'fallback' ? 'help' : 'spark'; }
  permanent(node: WorkflowNode) { return this.triggers.some(value => value.type === node.type && value.permanent); }
  languageName(code: string) { return this.languages.find(value => value.code === code)?.name || code; }
  availableTriggers() { return this.triggers.filter(value => !value.permanent); }
  groupedActions(group: string) { return this.actions.filter(value => value.group === group); }
  jumpTargets() { return workflowJumpTargets(this.document()?.nodes || []).filter(target => target.id !== this.editor()?.id); }
  jumpActive() {
    const node = this.editor(); if (!node?.enabled) return false;
    const targets = workflowJumpTargets(this.document()?.nodes || []);
    return this.editorInsertion
      ? targets.find(target => target.id === this.editorInsertion!.parentId)?.enabled ?? true
      : targets.find(target => target.id === node.id)?.ancestorsEnabled ?? true;
  }
  jumpError() { return jumpConfigError(this.editJump, this.jumpTargets(), this.jumpActive()); }
  surveyError() { return surveyConfigError(this.editSurvey); }
  jumpTargetLabel(node: WorkflowNode) {
    const target = this.find(node.config['target_node_id']);
    return target ? `Jump to #${target.number} ${target.name}` : 'Choose a target';
  }
  private find(id: string, nodes = this.document()?.nodes || []): WorkflowNode | null {
    for (const node of nodes) { if (node.id === id) return node; const child = this.find(id, node.children); if (child) return child; }
    return null;
  }
  private listFor(nodes: WorkflowNode[], parentId: string | null): WorkflowNode[] {
    if (!parentId) return nodes;
    const find = (values: WorkflowNode[]): WorkflowNode | undefined => {
      for (const value of values) { if (value.id === parentId) return value; const child = find(value.children); if (child) return child; }
      return undefined;
    };
    return find(nodes)?.children || [];
  }
  private editingValue() {
    const node = this.editor(); if (!node) return null;
    const result = structuredClone(node);
    if (result.type === 'send_message') result.config = structuredClone(this.editMessages);
    if (result.type === 'set_variable') result.config = structuredClone(this.editVariable);
    if (result.type === 'jump') result.config = structuredClone(this.editJump);
    if (result.type === 'ask_survey') result.config = structuredClone(this.editSurvey);
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
  private clearEditor() { this.editor.set(null); this.editorInsertion = null; this.baseline = ''; }
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
    this.editMessages = structuredClone(node.config['translations'] ? node.config as SendMessageConfig : { translations: {} });
    this.editVariable = node.type === 'set_variable' ? structuredClone(node.config as unknown as SetVariableConfig) : { variable_name: '', chat_script: '' };
    this.editJump = node.type === 'jump' ? { target_node_id: node.config['target_node_id'] || '' } : { target_node_id: '' };
    this.editSurvey = node.type === 'ask_survey' ? structuredClone(node.config as unknown as AskSurveyConfig) : { fields: [] };
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
    if (value.type === 'jump' && this.jumpError()) { this.error.set(this.jumpError()); return; }
    if (value.type === 'ask_survey' && this.surveyError()) { this.error.set(this.surveyError()); return; }
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
        if (index >= 0) values[index] = value; else values.forEach(node => replace(node.children));
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
    if (!this.canManage || !this.canDiscard()) return;
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
    if (!action.available || !this.insertion) return;
    const config = action.type === 'send_message' ? { translations: { [this.agent.default_language]: { messages: [''], quick_replies: [] } } }
      : action.type === 'set_variable' ? { variable_name: '', chat_script: '' }
      : action.type === 'jump' ? { target_node_id: '' }
      : action.type === 'ask_survey' ? { fields: [newSurveyField()] } : null;
    if (!config) return;
    const insertion = { ...this.insertion }; this.closeDialog();
    const node: WorkflowNode = { id: crypto.randomUUID(), kind: 'action', type: action.type, name: action.name, description: '', enabled: true,
      config, children: [] };
    this.setEditor(node, insertion);
  }
  async toggle(node: WorkflowNode) {
    if (this.permanent(node) || !this.canManage || !this.canDiscard()) return;
    const nodes = structuredClone(this.document()!.nodes);
    const visit = (values: WorkflowNode[]) => values.forEach(value => { if (value.id === node.id) value.enabled = !value.enabled; visit(value.children); });
    visit(nodes); await this.persist(nodes);
  }
  async remove(node: WorkflowNode, parentId: string | null) {
    if (this.permanent(node) || !this.canManage || !this.canDiscard()) return;
    if (!window.confirm(`Delete “${node.name}” and all its nested steps?`)) return;
    const nodes = structuredClone(this.document()!.nodes); const list = this.listFor(nodes, parentId);
    list.splice(list.findIndex(value => value.id === node.id), 1);
    if (await this.persist(nodes)) { if (node.id === this.rootId()) this.rootId.set(null); this.success.set('Step deleted.'); }
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
