"""Native Zendesk Switchboard and Bitrix24 Open Channels methods.

The existing bounded, pinned HTTPS transport owns TLS/DNS checks and never retries.
Provider bodies and credentials are deliberately excluded from errors and audit data.
"""
import base64
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class TransferResult:
    status: str
    error: str = ''


class ProviderClient:
    def __init__(self, transport):
        self.transport = transport

    def call(self, platform, config, credentials, method, resource, payload=None):
        headers = {'Content-Type': 'application/json'}
        if platform == 'zendesk':
            token = base64.b64encode((config['key_id'] + ':' + credentials['secret']).encode()).decode()
            headers['Authorization'] = 'Basic ' + token
            url = config['origin'] + '/sc/v2/apps/' + config['app_id'] + resource
        else:
            url = config['origin'] + '/rest/' + config['key_id'] + '/' + credentials['secret'] + '/' + resource + '.json'
        return self.transport.send(method, url, headers, json.dumps(payload).encode() if payload is not None else None, 15)

    @staticmethod
    def usable(result):
        return result.outcome == 'success' and isinstance(result.body, dict) and not ('error' in result.body or 'errors' in result.body or 'error_description' in result.body)

    def verify(self, platform, config, credentials):
        if platform == 'zendesk':
            r = self.call(platform, config, credentials, 'GET', '/switchboards/' + config['switchboard_id'] + '/switchboardIntegrations')
            rows = r.body.get('switchboardIntegrations', []) if self.usable(r) else []
            return any(isinstance(row, dict) and row.get('id') == config['target_id'] and row.get('integrationType') == 'zd:agentWorkspace' for row in rows)
        r = self.call(platform, config, credentials, 'POST', 'scope', {})
        scopes = r.body.get('result') if self.usable(r) else None
        return isinstance(scopes, list) and {'imopenlines', 'imbot'}.issubset(scopes)

    def conversation_exists(self, platform, config, credentials, external_id):
        if platform == 'zendesk':
            r = self.call(platform, config, credentials, 'GET', '/conversations/' + external_id)
            value = r.body.get('conversation') if self.usable(r) else None
        else:
            r = self.call(platform, config, credentials, 'POST', 'imopenlines.dialog.get', {'CHAT_ID': int(external_id)})
            value = r.body.get('result') if self.usable(r) else None
        return isinstance(value, dict) and str(value.get('id')) == external_id

    def transfer(self, platform, config, credentials, external_id):
        if platform == 'zendesk':
            r = self.call(platform, config, credentials, 'POST', '/conversations/' + external_id + '/passControl', {'switchboardIntegration': config['target_id']})
            success = self.usable(r) and r.status_code == 200
        else:
            r = self.call(platform, config, credentials, 'POST', 'imopenlines.bot.session.transfer', {
                'CHAT_ID': int(external_id), 'USER_ID': int(config['target_id']), 'CLIENT_ID': credentials['bot_client_id'], 'LEAVE': 'Y'})
            success = self.usable(r) and r.body.get('result') is True
        if success:
            return TransferResult('transferred')
        # An explicit API rejection is safe to report as failed. A transport timeout,
        # server failure or malformed success cannot establish which side owns the chat.
        rejected = (r.status_code is not None and 400 <= r.status_code < 500 and r.status_code != 408) or (
            r.outcome == 'success' and isinstance(r.body, dict) and ('error' in r.body or 'errors' in r.body or 'error_description' in r.body or r.body.get('result') is False))
        return TransferResult('failed' if rejected else 'uncertain', 'provider_rejected' if rejected else 'confirmation_unavailable')
