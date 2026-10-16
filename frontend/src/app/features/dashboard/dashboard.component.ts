import { DatePipe, TitleCasePipe } from '@angular/common';
import { Component, ElementRef, HostListener, OnDestroy, OnInit, ViewChild, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { ApiService, errorMessage } from '../../core/api.service';
import { Agent, AgentChange, AgentNetwork, AgentOptions, AgentVersion, RoleOptions } from '../../core/models';
import { BrandComponent } from '../../shared/brand.component';
import { IconComponent } from '../../shared/icon.component';
import { WorkflowsComponent } from '../workflows/workflows.component';
import { WorkflowTesterComponent } from '../workflows/tester/workflow-tester.component';

type Modal = 'choose' | 'create' | 'import' | 'delete' | 'version';
type Section = 'personalization' | 'languages' | 'identity' | 'voice';

@Component({
  selector: 'petrix-dashboard', imports: [FormsModule, DatePipe, TitleCasePipe, BrandComponent, IconComponent, WorkflowsComponent, WorkflowTesterComponent],
  templateUrl: './dashboard.component.html', styleUrl: './dashboard.component.css'
})
export class DashboardComponent implements OnInit, OnDestroy {
  readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  @ViewChild('agentDialog') private dialog?: ElementRef<HTMLDialogElement>;
  @ViewChild(WorkflowsComponent) private workflowEditor?: WorkflowsComponent;
  readonly settingsMode = this.route.snapshot.data['roles'] === true;
  readonly loading = signal(true);
  readonly sidebarOpen = signal(false);
  readonly dropdownOpen = signal(false);
  readonly languageOpen = signal(false);
  readonly error = signal('');
  readonly info = signal('');
  readonly busy = signal('');
  readonly agents = signal<Agent[]>([]);
  readonly selected = signal<Agent | null>(null);
  readonly canManage = signal(false);
  readonly options = signal<AgentOptions | null>(null);
  readonly usage = signal({ count: 0, agent_limit: 3, language_limit: 3, plan_name: 'Starter' });
  readonly activeTab = signal('Agent');
  readonly modal = signal<Modal | null>(null);
  readonly modalError = signal('');
  readonly versions = signal<AgentVersion[]>([]);
  readonly changes = signal<AgentChange[]>([]);
  readonly network = signal<AgentNetwork>({ nodes: [], edges: [] });
  readonly roles = signal<RoleOptions | null>(null);
  readonly tabLoading = signal(false);
  readonly hasMoreVersions = signal(false);
  readonly hasMoreChanges = signal(false);
  readonly versionDetail = signal<Awaited<ReturnType<ApiService['version']>> | null>(null);
  readonly testerBusy = signal(false);
  readonly testerOpen = signal(false);
  readonly testerResetKey = signal(0);
  readonly workflowRevision = signal(0);
  readonly menu = [
    { label: 'Advisor', icon: 'bolt' }, { label: 'Inbox', icon: 'inbox' },
    { label: 'AI agents', icon: 'spark' }, { label: 'Livechats', icon: 'chat' },
    { label: 'Tickets', icon: 'ticket' }, { label: 'People', icon: 'people' },
    { label: 'Analytics', icon: 'chart' }, { label: 'Perception', icon: 'eye' }
  ];
  readonly tabs = ['Agent', 'Knowledge', 'Workflows', 'Versions', 'Voice', 'Publish', 'Change log', 'Network'];
  readonly availableTabs = new Set(['Agent', 'Workflows', 'Versions', 'Voice', 'Change log', 'Network']);
  agentSearch = ''; languageSearch = ''; activeSection = 'personalization';
  persona = ''; llmEnabled = true; assignedLanguages: string[] = []; defaultLanguage = 'en';
  identityName = ''; identityFile: File | null = null; identityPreview = ''; identityRemove = false;
  voiceEnabled = false; voiceName = ''; voiceSpeed = 1;
  createName = 'New AI Agent'; createLanguage = 'en'; createFile: File | null = null; createPreview = '';
  importFile: File | null = null; importPreview: { name: string; languages: string[] } | null = null;
  confirmationName = ''; connectTarget = ''; rolesAdmin = true; rolesMember = false;
  private leavingConfirmed = false;

  async ngOnInit() { await this.load(); }
  ngOnDestroy() { this.revoke(this.createPreview); this.revoke(this.identityPreview); this.dialog?.nativeElement.close(); }
  get initials() { return this.initialsFor(this.api.context()?.workspace.name || 'Petrix AI'); }
  get userInitial() { return this.initialsFor(this.api.context()?.user.full_name || this.api.context()?.user.email || 'P'); }
  initialsFor(name: string) { return name.trim().split(/\s+/).filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase() || 'AI'; }
  languageName(code: string) { return this.options()?.languages.find(language => language.code === code)?.name || code; }
  filteredAgents() { return this.agents().filter(agent => agent.name.toLowerCase().includes(this.agentSearch.toLowerCase())); }
  filteredLanguages() { return this.options()?.languages.filter(language => language.name.toLowerCase().includes(this.languageSearch.toLowerCase())) || []; }
  atCapacity() { return this.usage().count >= this.usage().agent_limit; }
  outgoing() { return this.network().edges.filter(edge => edge.source_id === this.selected()?.id); }
  incoming() { return this.network().edges.filter(edge => edge.target_id === this.selected()?.id); }
  nodeName(id: string) { return this.network().nodes.find(node => node.id === id)?.name || 'AI agent'; }
  connectionTargets() { return this.network().nodes.filter(node => node.id !== this.selected()?.id && !this.outgoing().some(edge => edge.target_id === node.id)); }
  roleValue(role: string) { return this.roles()?.roles.find(value => value.role === role)?.manage_ai_agents || false; }

  dirty(section?: Section): boolean {
    const agent = this.selected();
    if (this.settingsMode) return !!this.roles() && (this.rolesAdmin !== this.roleValue('admin') || this.rolesMember !== this.roleValue('member'));
    if (!agent) return false;
    const states = {
      personalization: this.persona !== agent.persona || this.llmEnabled !== agent.llm_enabled,
      languages: this.defaultLanguage !== agent.default_language || [...this.assignedLanguages].sort().join(',') !== [...agent.languages].sort().join(','),
      identity: this.identityName !== agent.name || !!this.identityFile || this.identityRemove,
      voice: this.voiceEnabled !== agent.voice_enabled || this.voiceName !== agent.voice_name || Number(this.voiceSpeed) !== agent.voice_speed
    };
    return section ? states[section] : Object.values(states).some(Boolean) || !!this.workflowEditor?.dirty();
  }
  canLeave() { return this.leavingConfirmed || !this.dirty() || window.confirm('You have unsaved changes. Discard them and continue?'); }
  @HostListener('window:beforeunload', ['$event']) beforeUnload(event: BeforeUnloadEvent) { if (this.dirty()) { event.preventDefault(); event.returnValue = ''; } }
  @HostListener('document:keydown.escape') escape() { this.dropdownOpen.set(false); this.languageOpen.set(false); this.testerOpen.set(false); }
  @HostListener('document:click', ['$event']) outsideClick(event: MouseEvent) {
    const target = event.target instanceof Element ? event.target : null;
    if (!target?.closest('.agent-picker')) this.dropdownOpen.set(false);
    if (!target?.closest('.language-picker')) this.languageOpen.set(false);
  }
  async load() {
    this.loading.set(true); this.error.set('');
    try {
      const [options, result, roles] = await Promise.all([this.api.agentOptions(), this.api.agents(), this.settingsMode ? this.api.roles() : Promise.resolve(null)]);
      this.options.set(options); this.agents.set(result.agents); this.canManage.set(result.can_manage); this.usage.set(result.usage);
      if (roles) { this.roles.set(roles); this.rolesAdmin = this.roleValue('admin'); this.rolesMember = this.roleValue('member'); }
      let saved = '';
      try { saved = sessionStorage.getItem(`petrix-agent:${this.api.context()!.workspace.id}`) || ''; } catch { /* Storage is optional. */ }
      const preferred = this.route.snapshot.queryParamMap.get('agent') || saved;
      const agent = result.agents.find(value => value.id === preferred) || result.agents[0];
      if (agent) this.applyAgent(agent); else this.selected.set(null);
      const tab = this.route.snapshot.queryParamMap.get('tab');
      if (tab && this.availableTabs.has(tab)) this.activeTab.set(tab);
      if (!this.settingsMode && agent) await this.loadTab();
    } catch (error) { this.error.set(errorMessage(error)); }
    finally { this.loading.set(false); }
  }
  private applyAgent(agent: Agent) {
    this.workflowRevision.set(0);
    this.selected.set(agent); this.loadForm('personalization', agent); this.loadForm('languages', agent);
    this.loadForm('identity', agent); this.loadForm('voice', agent); this.resetTester();
    this.versions.set([]); this.changes.set([]); this.network.set({ nodes: [], edges: [] });
    try { sessionStorage.setItem(`petrix-agent:${agent.workspace_id}`, agent.id); } catch { /* Storage is optional. */ }
  }
  private loadForm(section: Section, agent: Agent) {
    if (section === 'personalization') { this.persona = agent.persona; this.llmEnabled = agent.llm_enabled; }
    if (section === 'languages') { this.assignedLanguages = [...agent.languages]; this.defaultLanguage = agent.default_language; }
    if (section === 'identity') { this.identityName = agent.name; this.identityFile = null; this.identityRemove = false; this.revoke(this.identityPreview); this.identityPreview = ''; }
    if (section === 'voice') { this.voiceEnabled = agent.voice_enabled; this.voiceName = agent.voice_name; this.voiceSpeed = agent.voice_speed; }
  }
  async selectAgent(agent: Agent) {
    if (agent.id === this.selected()?.id) { this.dropdownOpen.set(false); return; }
    if (this.busy() || this.testerBusy() || !this.canLeave()) return;
    this.applyAgent(agent); this.dropdownOpen.set(false); this.error.set(''); this.info.set('');
    await this.syncUrl(); await this.loadTab();
  }
  private syncUrl() { return this.router.navigate([], { relativeTo: this.route, queryParams: { agent: this.selected()?.id || null, tab: this.activeTab() }, replaceUrl: true }); }
  async setTab(tab: string) {
    if (!this.availableTabs.has(tab) || !this.selected() || this.busy()) return;
    if (tab !== this.activeTab() && this.workflowEditor && !this.workflowEditor.canDiscard()) return;
    this.activeTab.set(tab); this.error.set(''); await this.syncUrl(); await this.loadTab();
  }
  async loadTab(append = false) {
    const agent = this.selected(); if (!agent) return;
    this.tabLoading.set(true);
    try {
      if (this.activeTab() === 'Versions') {
        const result = await this.api.versions(agent.id, append ? this.versions().length : 0);
        if (this.selected()?.id === agent.id) { this.versions.set(append ? [...this.versions(), ...result.versions] : result.versions); this.hasMoreVersions.set(result.has_more); }
      }
      if (this.activeTab() === 'Change log') {
        const result = await this.api.changes(agent.id, append ? this.changes().length : 0);
        if (this.selected()?.id === agent.id) { this.changes.set(append ? [...this.changes(), ...result.changes] : result.changes); this.hasMoreChanges.set(result.has_more); }
      }
      if (this.activeTab() === 'Network') { const result = await this.api.network(agent.id); if (this.selected()?.id === agent.id) this.network.set(result); }
    } catch (error) { this.error.set(errorMessage(error)); }
    finally { this.tabLoading.set(false); }
  }
  jump(section: string) { this.activeSection = section; document.getElementById(section)?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  toggleLanguage(code: string) {
    if (!this.canManage() || this.busy()) return;
    if (this.assignedLanguages.includes(code)) {
      if (this.assignedLanguages.length === 1) { this.error.set('Keep at least one language selected.'); return; }
      this.assignedLanguages = this.assignedLanguages.filter(value => value !== code);
      if (this.defaultLanguage === code) this.defaultLanguage = this.assignedLanguages[0];
    } else if (this.assignedLanguages.length < this.usage().language_limit) this.assignedLanguages = [...this.assignedLanguages, code];
    else this.error.set(`Your plan allows ${this.usage().language_limit} languages per agent.`);
  }
  private revoke(value: string) { if (value.startsWith('blob:')) URL.revokeObjectURL(value); }
  chooseAvatar(event: Event, target: 'create' | 'identity') {
    const input = event.target as HTMLInputElement; const file = input.files?.[0]; if (!file) return;
    if (file.size > 10 * 1024 * 1024 || (file.type && !['image/png', 'image/jpeg', 'image/gif'].includes(file.type))) {
      (target === 'create' ? this.modalError : this.error).set('Choose a PNG, JPEG, or GIF image up to 10 MB.'); input.value = ''; return;
    }
    if (target === 'create') { this.revoke(this.createPreview); this.createFile = file; this.createPreview = URL.createObjectURL(file); }
    else { this.revoke(this.identityPreview); this.identityFile = file; this.identityPreview = URL.createObjectURL(file); this.identityRemove = false; }
    input.value = '';
  }
  removeAvatar(target: 'create' | 'identity') {
    if (target === 'create') { this.revoke(this.createPreview); this.createPreview = ''; this.createFile = null; }
    else { this.revoke(this.identityPreview); this.identityPreview = ''; this.identityFile = null; this.identityRemove = !!this.selected()?.avatar_url; }
  }
  openModal(modal: Modal) {
    if (modal === 'choose' && !this.modal() && !this.canLeave()) return;
    this.dropdownOpen.set(false); this.modalError.set(''); this.modal.set(modal);
    if (modal === 'choose') { this.createName = 'New AI Agent'; this.createLanguage = 'en'; this.removeAvatar('create'); this.importFile = null; this.importPreview = null; }
    if (modal === 'delete') this.confirmationName = '';
    if (!this.dialog?.nativeElement.open) this.dialog?.nativeElement.showModal();
    setTimeout(() => this.dialog?.nativeElement.querySelector<HTMLElement>('input:not([type="file"]),button')?.focus(), 0);
  }
  closeModal() { if (this.busy()) return; this.modal.set(null); this.dialog?.nativeElement.close(); this.removeAvatar('create'); this.importFile = null; this.importPreview = null; }
  onDialogCancel(event: Event) { event.preventDefault(); this.closeModal(); }
  onDialogClick(event: MouseEvent) { if (event.target === this.dialog?.nativeElement) { const bounds = this.dialog.nativeElement.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) this.closeModal(); } }
  async chooseImport(event: Event) {
    const file = (event.target as HTMLInputElement).files?.[0]; this.importFile = null; this.importPreview = null; this.modalError.set(''); if (!file) return;
    if (file.size > 15 * 1024 * 1024) { this.modalError.set('Choose a Petrix agent JSON export up to 15 MB.'); return; }
    try {
      const packageData = JSON.parse(await file.text());
      if (packageData.format !== 'petrix-agent' || packageData.schema_version !== 1 || typeof packageData.agent?.name !== 'string' || !Array.isArray(packageData.agent?.languages) || !packageData.agent.languages.every((value: unknown) => typeof value === 'string')) throw new Error();
      this.importFile = file; this.importPreview = { name: packageData.agent.name, languages: packageData.agent.languages };
    } catch { this.modalError.set('Choose a valid Petrix AI agent export. Other platform formats need to be converted first.'); }
  }
  async createAgent() {
    if (this.busy() || !this.createName.trim() || !this.canManage()) return;
    this.busy.set('create'); this.modalError.set('');
    try {
      const data = new FormData(); data.append('name', this.createName.trim()); data.append('default_language', this.createLanguage); if (this.createFile) data.append('avatar', this.createFile);
      const agent = await this.api.createAgent(data); this.acceptCreated(agent); this.busy.set(''); this.closeModal(); this.info.set('AI agent created. Make it yours below.');
    } catch (error) { this.modalError.set(errorMessage(error)); }
    finally { this.busy.set(''); }
  }
  async importAgent() {
    if (!this.importFile || this.busy() || !this.canManage()) return;
    this.busy.set('import'); this.modalError.set('');
    try { const agent = await this.api.importAgent(this.importFile); this.acceptCreated(agent); this.busy.set(''); this.closeModal(); this.info.set('AI agent imported successfully.'); }
    catch (error) { this.modalError.set(errorMessage(error)); }
    finally { this.busy.set(''); }
  }
  private acceptCreated(agent: Agent) {
    this.agents.update(values => [...values, agent]); this.usage.update(value => ({ ...value, count: this.agents().length }));
    this.applyAgent(agent); this.activeTab.set('Agent'); void this.syncUrl();
  }
  private acceptDeleted(id: string) {
    this.agents.update(values => values.filter(value => value.id !== id)); this.usage.update(value => ({ ...value, count: this.agents().length }));
    const next = this.agents()[0]; if (next) this.applyAgent(next); else { this.selected.set(null); this.resetTester(); }
    this.activeTab.set('Agent'); void this.syncUrl();
  }
  async save(section: Section) {
    const agent = this.selected(); if (!agent || this.busy() || !this.canManage() || !this.dirty(section)) return;
    this.busy.set(section); this.error.set(''); this.info.set('');
    try {
      let body: object | FormData;
      if (section === 'identity') { const data = new FormData(); data.append('name', this.identityName.trim()); data.append('expected_revision', String(agent.revision)); data.append('remove_avatar', String(this.identityRemove)); if (this.identityFile) data.append('avatar', this.identityFile); body = data; }
      else if (section === 'personalization') body = { expected_revision: agent.revision, llm_enabled: this.llmEnabled, persona: this.persona };
      else if (section === 'languages') body = { expected_revision: agent.revision, languages: this.assignedLanguages, default_language: this.defaultLanguage };
      else body = { expected_revision: agent.revision, voice_enabled: this.voiceEnabled, voice_name: this.voiceName, voice_speed: Number(this.voiceSpeed) };
      const updated = await this.api.saveAgent(agent.id, section, body); this.selected.set(updated); this.agents.update(agents => agents.map(value => value.id === updated.id ? updated : value)); this.loadForm(section, updated); this.info.set('Changes saved.');
    } catch (error) { this.error.set(errorMessage(error)); }
    finally { this.busy.set(''); }
  }
  async exportAgent() {
    const agent = this.selected(); if (!agent || this.busy()) return; this.busy.set('export');
    try { const blob = await this.api.exportAgent(agent.id); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `petrix-agent-${agent.id}.json`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
    catch (error) { this.error.set(errorMessage(error)); } finally { this.busy.set(''); }
  }
  async deleteAgent() {
    const agent = this.selected(); if (!agent || this.confirmationName !== agent.name || this.busy()) return; this.busy.set('delete');
    try { await this.api.deleteAgent(agent, this.confirmationName); this.acceptDeleted(agent.id); this.busy.set(''); this.closeModal(); this.info.set('AI agent deleted. Conversation history has been retained.'); }
    catch (error) { this.modalError.set(errorMessage(error)); } finally { this.busy.set(''); }
  }
  async viewVersion(number: number) {
    const agent = this.selected(); if (!agent) return; this.versionDetail.set(null); this.openModal('version'); this.busy.set('version');
    try { this.versionDetail.set(await this.api.version(agent.id, number)); } catch (error) { this.modalError.set(errorMessage(error)); } finally { this.busy.set(''); }
  }
  async restoreVersion() {
    const agent = this.selected(); const detail = this.versionDetail(); if (!agent || !detail || this.busy() || !this.canManage() || !this.canLeave()) return;
    this.busy.set('restore'); this.modalError.set('');
    try { const updated = await this.api.restoreVersion(agent, detail.number); this.agents.update(values => values.map(value => value.id === updated.id ? updated : value)); this.applyAgent(updated); this.busy.set(''); this.closeModal(); this.info.set(`Settings from version ${detail.number} restored.`); await this.loadTab(); }
    catch (error) { this.modalError.set(errorMessage(error)); } finally { this.busy.set(''); }
  }
  async connectAgent() {
    const agent = this.selected(); if (!agent || !this.connectTarget || this.busy()) return; this.busy.set('network'); this.error.set('');
    try { await this.api.connectAgent(agent.id, this.connectTarget); this.connectTarget = ''; await this.loadTab(); this.info.set('Agent connection added.'); }
    catch (error) { this.error.set(errorMessage(error)); } finally { this.busy.set(''); }
  }
  async disconnectAgent(edge: string) {
    const agent = this.selected(); if (!agent || this.busy()) return; this.busy.set('network');
    try { await this.api.disconnectAgent(agent.id, edge); await this.loadTab(); this.info.set('Agent connection removed.'); }
    catch (error) { this.error.set(errorMessage(error)); } finally { this.busy.set(''); }
  }
  async saveRoles() {
    if (this.busy() || !this.dirty()) return; this.busy.set('roles'); this.error.set('');
    try { await this.api.saveRoles({ admin: this.rolesAdmin, member: this.rolesMember }); this.roles.set(await this.api.roles()); await this.api.loadContext(); this.info.set('Roles and permissions saved.'); }
    catch (error) { this.error.set(errorMessage(error)); } finally { this.busy.set(''); }
  }
  resetTester() { this.testerResetKey.update(value => value + 1); }
  async openSettings() { this.sidebarOpen.set(false); await this.router.navigateByUrl('/app/settings/workspace/roles'); }
  async openAgents() { this.sidebarOpen.set(false); await this.router.navigateByUrl('/app/agents'); }
  futureFeature(label: string) { this.info.set(`${label} will be available in an upcoming milestone.`); this.sidebarOpen.set(false); }
  async logout() {
    if (this.busy() || !this.canLeave()) return; this.busy.set('logout'); this.error.set('');
    try { await this.api.logout(); this.leavingConfirmed = true; await this.router.navigateByUrl('/login'); }
    catch (error) { this.error.set(errorMessage(error)); } finally { this.busy.set(''); }
  }
}
