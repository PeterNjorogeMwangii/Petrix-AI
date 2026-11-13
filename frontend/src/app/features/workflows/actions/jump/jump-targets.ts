import { JumpConfig, WorkflowNode } from '../../workflow.models';
import { actionPaths } from '../../workflow-tree';

export interface JumpTarget {
  id: string;
  number?: number;
  name: string;
  kind: 'trigger' | 'action';
  type: string;
  path: string;
  enabled: boolean;
  ancestorsEnabled: boolean;
  unavailableReason?: string;
}

export function workflowJumpTargets(nodes: WorkflowNode[]): JumpTarget[] {
  const targets: JumpTarget[] = [];
  const visit = (values: WorkflowNode[], path: string[], ancestorsEnabled: boolean) => {
    for (const node of values) {
      const enabled = ancestorsEnabled && node.enabled;
      targets.push({ id: node.id, number: node.number, name: node.name, kind: node.kind, type: node.type,
        path: path.join(' / '), enabled, ancestorsEnabled });
      visit(node.children, [...path, node.name], enabled);
      if (node.paths?.length) {
        for (const branch of actionPaths(node)) {
          visit(branch.children, [...path, node.name, [branch.label, branch.name].filter(Boolean).join(' · ')], enabled);
        }
      }
    }
  };
  visit(nodes, [], true);
  return targets.sort((a, b) => (a.number ?? Infinity) - (b.number ?? Infinity));
}

export function jumpConfigError(config: JumpConfig, targets: JumpTarget[], active = true): string {
  if (!config.target_node_id) return 'Choose a trigger or action to jump to.';
  const target = targets.find(value => value.id === config.target_node_id);
  if (!target) return 'Choose an existing step in this agent’s workflow. A Jump cannot target itself.';
  if (target.unavailableReason) return target.unavailableReason;
  if (active && !target.enabled) return 'Enable the target and its parent steps, or disable this Jump first.';
  return '';
}
