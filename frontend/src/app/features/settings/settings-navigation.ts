export const settingsGroups = [
  { label: 'Workspace', key: 'workspace', items: [
    ['general', 'General'], ['teamspaces', 'Teamspaces'], ['plan-billing', 'Plan & billing'],
    ['roles', 'Roles & permissions'], ['users', 'Users'], ['voice-api', 'Voice API'], ['teams', 'Teams'],
    ['ip-allowlist', 'IP allowlist'], ['whitelabelling', 'Whitelabelling']
  ] },
  { label: 'Teamspace', key: 'teamspace', items: [
    ['ai-analysis', 'AI analysis'], ['banning', 'Banning'], ['chat-assignment', 'Chat assignment'],
    ['custom-fields', 'Custom fields'], ['database-connections', 'Database connections'],
    ['email-domains', 'Email domains'], ['email-templates', 'Email templates'], ['exports', 'Exports'],
    ['inbox-copilot', 'Inbox co-pilot'], ['tags', 'Tags']
  ] },
  { label: 'Livechat', key: 'livechat', items: [['canned-replies', 'Canned replies']] },
  { label: 'Tickets', key: 'tickets', items: [['overdue-rules', 'Overdue rules']] },
  { label: 'People', key: 'people', items: [['iframe-profiles', 'iFrame profiles']] },
  { label: 'Analytics', key: 'analytics', items: [['custom-analytics', 'Custom analytics']] },
  { label: 'My profile', key: 'my-profile', items: [['account', 'Account'], ['aliases', 'Aliases']] }
];
export const settingsPages = settingsGroups.flatMap(group => group.items.map(([slug, label]) => ({
  key: `${group.key}/${slug}`, label, group: group.label,
  implemented: slug === 'roles' || slug === 'database-connections'
})));
