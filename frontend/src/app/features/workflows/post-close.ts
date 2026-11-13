import { WorkflowNode } from './workflow.models';

export const POST_CLOSE_ACTIONS = ['collect_feedback','create_ticket','jump','branch','set_variable','switch_language','send_api','llm_processing','auto_fill_field','auto_team_match','sql_query','send_graph','close_chat'];

export class PostCloseFlow {
  readonly nodes = new Map<string, WorkflowNode>();
  readonly owners = new Map<string, string>();
  private readonly after = new Map<string, string | null>();
  private readonly triggerEntry = new Map<string, string | null>();
  constructor(nodes: WorkflowNode[]) {
    const collect = (values: WorkflowNode[], continuation: string | null = null, rootType = '') => {
      values.forEach((node, index) => {
        const owner = rootType || node.type; const after = values[index + 1]?.id || continuation;
        this.nodes.set(node.id, node); this.owners.set(node.id, owner); this.after.set(node.id, after);
        this.triggerEntry.set(node.id, node.children[0]?.id || continuation);
        collect(node.children, node.kind === 'trigger' ? continuation : after, owner);
        for (const path of node.paths || []) collect(path.children, after, owner);
      });
    };
    collect(nodes);
  }
  issue(targetId: string) {
    const target = this.nodes.get(targetId); if (!target) return 'Choose an existing step.';
    const pending: (string | null | undefined)[] = [target.kind === 'trigger' ? this.triggerEntry.get(target.id) : target.id];
    const visited = new Set<string>();
    while (pending.length) {
      const id = pending.pop(); if (!id || visited.has(id)) continue;
      visited.add(id); const node = this.nodes.get(id)!;
      if (node.kind === 'trigger') return 'This destination waits for an incoming trigger.';
      if (!POST_CLOSE_ACTIONS.includes(node.type)) return `${node.name} cannot run after chat closure.`;
      if (node.type === 'jump') {
        const next = this.nodes.get(node.config['target_node_id']); if (!next) return 'This destination has a missing Jump target.';
        pending.push(next.kind === 'trigger' ? this.triggerEntry.get(next.id) : next.id);
      } else if (['branch','send_api','sql_query','llm_processing'].includes(node.type)) {
        for (const path of node.paths || []) pending.push(path.children[0]?.id || this.after.get(id));
      } else pending.push(node.children[0]?.id || this.after.get(id));
    }
    return '';
  }
}
