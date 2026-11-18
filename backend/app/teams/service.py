from fastapi import HTTPException
from sqlalchemy import select
from app.core.security import utcnow
from app.models import AgentChange, SupportTeam, Membership, AgentConversation, Agent, WorkflowDocument
from app.teams.matching import match_team, normalize


def definitions(db, workspace_id):
    return db.scalars(select(SupportTeam).where(SupportTeam.workspace_id == workspace_id,
        SupportTeam.archived.is_(False)).order_by(SupportTeam.created_at, SupportTeam.id)).all()


def payload(team):
    return {key: getattr(team, key) for key in ('id', 'name', 'description', 'alternative_names',
        'revision', 'created_at', 'updated_at','member_ids','role_id','teamspace_ids')}


def unique_names(db, workspace_id, body, identifier=None):
    requested = {normalize(name) for name in [body.name] + body.alternative_names}
    for team in definitions(db, workspace_id):
        if team.id == identifier:
            continue
        existing = {normalize(name) for name in [team.name] + team.alternative_names}
        if requested & existing:
            raise HTTPException(409, 'A team name or alternative name is already used by “{}”. Use a unique name.'.format(team.name))


def audit(db, workspace_id, actor_id, action, name):
    db.add(AgentChange(workspace_id=workspace_id, actor_id=actor_id, agent_name='', action='team_' + action,
        summary='Support team “{}” {}.'.format(name, action)))


def get_team(db, workspace_id, identifier, expected):
    team = db.scalar(select(SupportTeam).where(SupportTeam.id == identifier,
        SupportTeam.workspace_id == workspace_id, SupportTeam.archived.is_(False))
        .with_for_update().execution_options(populate_existing=True))
    if not team:
        raise HTTPException(404, 'Team not found.')
    if team.revision != expected:
        raise HTTPException(409, 'This team changed in another session. Reload before saving.')
    return team


def create(db, workspace_id, actor_id, body):
    if len(definitions(db, workspace_id)) >= 200:
        raise HTTPException(409, 'A workspace supports up to 200 active teams.')
    unique_names(db, workspace_id, body)
    members=validated_members(db,workspace_id,body.member_ids or [])
    extra=role_assignment(db,workspace_id,actor_id,body)
    team = SupportTeam(workspace_id=workspace_id, member_ids=members, **body.model_dump(mode='json',exclude={'member_ids','role_id','teamspace_ids'}),**extra)
    db.add(team)
    audit(db, workspace_id, actor_id, 'created', body.name)
    db.commit()
    return payload(team)


def update(db, workspace_id, actor_id, identifier, body):
    team = get_team(db, workspace_id, identifier, body.expected_revision)
    unique_names(db, workspace_id, body, identifier)
    values = body.model_dump(mode='json', exclude={'expected_revision','member_ids','role_id','teamspace_ids'})
    if {'role_id','teamspace_ids'}&body.model_fields_set:values.update(role_assignment(db,workspace_id,actor_id,body))
    if body.member_ids is not None:
        values['member_ids']=validated_members(db,workspace_id,body.member_ids)
        if values['member_ids']!=team.member_ids:protect_live_members(db,workspace_id,team)
    if any(getattr(team, key) != value for key, value in values.items()):
        for key, value in values.items():
            setattr(team, key, value)
        team.revision += 1
        team.updated_at = utcnow()
        audit(db, workspace_id, actor_id, 'updated', team.name)
    db.commit()
    return payload(team)


def remove(db, workspace_id, actor_id, identifier, body):
    team = get_team(db, workspace_id, identifier, body.expected_revision)
    from app.workflows.numbering import walk
    for nodes in db.scalars(select(WorkflowDocument.nodes).join(Agent,Agent.id==WorkflowDocument.agent_id).where(Agent.workspace_id==workspace_id)):
        if any(n['kind']=='action' and n['type']=='live_agent_handoff' and str(team.id) in n['config']['team_ids'] for n in walk(nodes)):
            raise HTTPException(409,'This team is used by Live Agent Handoff. Remove its workflow references before deleting it.')
    from app.tickets.settings import references
    from app.models import SupportTicket
    if references(db,workspace_id,'teams',team.id):raise HTTPException(409,'Remove this team from Create Ticket actions before deleting it.')
    if db.scalar(select(SupportTicket.id).where(SupportTicket.workspace_id==workspace_id,SupportTicket.assignee_type=='team',SupportTicket.assignee_id==team.id,SupportTicket.status.in_(['open','in_progress']))):
        raise HTTPException(409,'Reassign or finish this team’s open tickets before deleting it.')
    protect_live_members(db,workspace_id,team)
    team.archived = True
    team.revision += 1
    team.updated_at = utcnow()
    audit(db, workspace_id, actor_id, 'deleted', team.name)
    db.commit()


class TeamMatcher:
    def __init__(self, db, workspace_id):
        self.db, self.workspace_id = db, workspace_id
        self._teams = None

    def match(self, text):
        # A bounded, workspace-scoped snapshot shared by every action in this event.
        if self._teams is None:
            with self.db.begin_nested():
                self._teams = [{'id': str(team.id), 'name': team.name, 'alternative_names': list(team.alternative_names)}
                    for team in definitions(self.db, self.workspace_id)]
        return match_team(text, self._teams)


def validated_members(db,workspace_id,ids):
    from app.inbox.service import assignment,ensure_profile
    assignment(db,workspace_id,lock=True)
    identifiers=[str(value) for value in ids]
    current={str(value) for value in db.scalars(select(Membership.user_id).where(Membership.workspace_id==workspace_id))}
    if not set(identifiers)<=current:raise HTTPException(422,'Choose members of this workspace for the team.')
    import uuid
    for identifier in identifiers:ensure_profile(db,workspace_id,uuid.UUID(identifier))
    return identifiers


def protect_live_members(db,workspace_id,team):
    from app.inbox.service import assignment
    assignment(db,workspace_id,lock=True)
    for conversation in db.scalars(select(AgentConversation).where(AgentConversation.workspace_id==workspace_id,AgentConversation.live_status.in_(['pending','active']))):
        if set(team.member_ids or []).intersection(conversation.live_context.get('eligible_ids',[])):
            raise HTTPException(409,'This team has a pending or active live chat. Finish those conversations before changing its members or deleting it.')


def role_assignment(db,workspace_id,actor_id,body):
    from app.workspace_settings import permissions as p
    from app.models import WorkspaceRole,Teamspace
    role=db.get(WorkspaceRole,body.role_id) if body.role_id else None
    if body.role_id and (not role or role.workspace_id!=workspace_id or role.kind!='team'):raise HTTPException(422,'Select a Team role from this workspace.')
    if role and not p.allowed(db,workspace_id,'manage_workspace_roles',actor_id) and not set(role.permissions)<=p.workspace_grants(db,workspace_id,actor_id):raise HTTPException(403,'You cannot assign a team role with permissions you do not hold.')
    spaces=None if body.teamspace_ids is None else list(map(str,body.teamspace_ids))
    if spaces is not None and not set(spaces)<={str(i) for i in db.scalars(select(Teamspace.id).where(Teamspace.workspace_id==workspace_id))}:raise HTTPException(422,'Select teamspaces in this workspace.')
    return {'role_id':role.id if role else None,'teamspace_ids':spaces}
