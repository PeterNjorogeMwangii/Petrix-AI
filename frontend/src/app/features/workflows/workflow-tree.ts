import { BranchConfig, SendAPIConfig, SendAttachmentConfig, ShowCarouselConfig, WorkflowNode } from './workflow.models';

export function childLists(node: WorkflowNode): WorkflowNode[][] {
  return [node.children, ...(node.paths || []).map(path => path.children)];
}

export function visitWorkflow(nodes: WorkflowNode[], visit: (node: WorkflowNode) => void): void {
  for (const node of nodes) {
    visit(node);
    for (const list of childLists(node)) visitWorkflow(list, visit);
  }
}

export function findWorkflowNode(nodes: WorkflowNode[], id: string): WorkflowNode | null {
  for (const node of nodes) {
    if (node.id === id) return node;
    for (const list of childLists(node)) {
      const found = findWorkflowNode(list, id);
      if (found) return found;
    }
  }
  return null;
}

export function workflowList(nodes: WorkflowNode[], parentId: string | null): WorkflowNode[] | null {
  if (!parentId) return nodes;
  for (const node of nodes) {
    if (node.id === parentId) return node.children;
    const path = node.paths?.find(path => path.id === parentId);
    if (path) return path.children;
    for (const list of childLists(node)) {
      const found = workflowList(list, parentId);
      if (found) return found;
    }
  }
  return null;
}

export function containerEnabled(nodes: WorkflowNode[], id: string | null, ancestors = true): boolean | undefined {
  if (!id) return true;
  for (const node of nodes) {
    const enabled = ancestors && node.enabled;
    if (node.id === id || node.paths?.some(path => path.id === id)) return enabled;
    for (const list of childLists(node)) {
      const found = containerEnabled(list, id, enabled);
      if (found !== undefined) return found;
    }
  }
  return undefined;
}

export function branchPaths(node: WorkflowNode) {
  const config = node.config as unknown as BranchConfig;
  const conditions = config.conditions.map((condition, index) => ({
    id: condition.id, label: index === 0 ? 'If' : 'Else if', name: condition.name, script: condition.chat_script
  }));
  return [...conditions, { id: config.else_path_id, label: 'Else', name: '', script: '' }].map(path => ({
    ...path, description: path.label === 'Else' ? 'When no condition matches' : '', children: node.paths?.find(value => value.id === path.id)?.children || []
  }));
}

export function actionPaths(node: WorkflowNode) {
  if (node.type === 'branch') return branchPaths(node);
  if (!['send_api', 'sql_query', 'llm_processing'].includes(node.type)) return [];
  const config = node.config as unknown as SendAPIConfig;
  return [
    { id: config.success_path_id, label: 'Success', name: '', script: '', description: node.type === 'llm_processing' ? 'Generated text saved to the resulting variable' : node.type === 'sql_query' ? 'Query completed, including empty results' : 'Request succeeded · HTTP 2xx' },
    { id: config.error_path_id, label: 'Error', name: '', script: '', description: node.type === 'llm_processing' ? 'The model or input could not be processed' : node.type === 'sql_query' ? 'The query or connection failed' : 'Request failed or returned an error' },
    { id: config.timeout_path_id, label: 'Timeout', name: '', script: '', description: node.type === 'llm_processing' ? 'The model exceeded its time limit' : node.type === 'sql_query' ? 'The query exceeded its time limit' : 'The request timed out' }
  ].map(path => ({ ...path, children: node.paths?.find(value => value.id === path.id)?.children || [] }));
}


export function uploadedAssetIds(node: WorkflowNode): string[] {
  if (node.type === 'send_attachment') return Object.values((node.config as unknown as SendAttachmentConfig).translations)
    .filter(value => value.source === 'upload' && !!value.asset_id).map(value => value.asset_id!);
  if (node.type === 'show_carousel') return Object.values((node.config as unknown as ShowCarouselConfig).translations)
    .flatMap(value => value.cards.flatMap(card => card.image?.source === 'upload' && card.image.asset_id ? [card.image.asset_id] : []));
  return [];
}
