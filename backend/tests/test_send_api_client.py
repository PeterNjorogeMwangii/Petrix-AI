"""Real HTTPS transport, response bounds, and endpoint isolation."""
from datetime import datetime, timedelta, timezone
import gzip
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import ssl
from threading import Thread
from types import SimpleNamespace
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import httpx
import pytest

from app.workflows.actions.send_api.client import SendAPIClient


def response(status=200, body=b'{"status":"resolved"}', content_type='application/json', **headers):
    return httpx.Response(status, headers={'content-type': content_type, **headers}, stream=httpx.ByteStream(body))


def client(handler, addresses=None, **kwargs):
    return SendAPIClient(transport_factory=lambda: httpx.MockTransport(handler),
        resolver=lambda *_: addresses if addresses is not None else ['8.8.8.8'], **kwargs)


@pytest.mark.parametrize('status,raw,content_type,expected,outcome', [
    (200, b'{"status":"resolved"}', 'application/json', {'status': 'resolved'}, 'success'),
    (201, b'[1,true,null]', 'application/vnd.api+json', [1, True, None], 'success'),
    (200, b'"hello"', 'application/json; charset=utf-8', 'hello', 'success'),
    (200, b'null', 'application/json', None, 'success'), (204, b'', 'application/json', None, 'success'),
    (200, b'Plain response', 'text/plain', 'Plain response', 'success'),
    (404, b'{"message":"Missing"}', 'application/json', {'message': 'Missing'}, 'error'),
    (503, b'Try later', 'text/plain', 'Try later', 'error'), (302, b'Moved', 'text/plain', 'Moved', 'error')])
def test_http_status_and_response_body_are_preserved(status, raw, content_type, expected, outcome):
    result = client(lambda _: response(status, raw, content_type)).send('GET', 'https://api.example.test', {}, None, 30)
    assert result.outcome == outcome and result.status_code == status and result.body == expected
    assert result.variable()['ok'] == (outcome == 'success')


def test_dns_is_checked_once_and_address_is_pinned_with_original_tls_and_host_names():
    requests, resolutions = [], []
    def resolve(host, port):
        resolutions.append((host, port)); return ['8.8.8.8']
    def handle(request):
        requests.append(request); return response()
    api = SendAPIClient(resolver=resolve, transport_factory=lambda: httpx.MockTransport(handle))
    assert api.send('POST', 'https://api.example.test:8443/path?x=1', {'Authorization': 'Bearer PRIVATE'}, b'{}', 30).outcome == 'success'
    request = requests[0]
    assert resolutions == [('api.example.test', 8443)]
    assert request.url.host == '8.8.8.8' and request.url.port == 8443
    assert request.headers['host'] == 'api.example.test:8443' and request.extensions['sni_hostname'] == 'api.example.test'
    assert request.headers['accept-encoding'] == 'identity' and request.content == b'{}'


@pytest.mark.parametrize('addresses', [['127.0.0.1'], ['10.0.0.1'], ['172.16.0.1'], ['192.168.1.1'], ['169.254.169.254'],
    ['0.0.0.0'], ['224.0.0.1'], ['100.64.0.1'], ['::1'], ['fe80::1'], ['fc00::1'], ['::ffff:127.0.0.1'], ['8.8.8.8','10.0.0.1']])
def test_nonpublic_destinations_and_mixed_dns_answers_do_not_reach_transport(addresses):
    calls = []
    result = client(lambda request: calls.append(request), addresses=addresses).send('GET', 'https://api.example.test', {}, None, 30)
    assert result.outcome == 'error' and result.error == 'blocked_endpoint' and calls == []


def test_only_exact_operator_allowed_hosts_may_use_private_networks():
    calls = []
    api = client(lambda request: calls.append(request) or response(), addresses=['10.10.0.4'], allowed_private_hosts=['internal.bank.test'])
    assert api.send('GET', 'https://internal.bank.test', {}, None, 30).outcome == 'success'
    assert api.send('GET', 'https://other.internal.bank.test', {}, None, 30).error == 'blocked_endpoint'
    assert api.send('GET', 'https://internal.bank.test.attacker.test', {}, None, 30).error == 'blocked_endpoint'
    assert len(calls) == 1


@pytest.mark.parametrize('address', ['169.254.169.254','fe80::1','0.0.0.0','224.0.0.1'])
def test_metadata_and_special_addresses_remain_blocked_with_private_host_allowance(address):
    result = client(lambda _: response(), addresses=[address], allowed_private_hosts=['internal.bank.test']).send('GET', 'https://internal.bank.test', {}, None, 30)
    assert result.error == 'blocked_endpoint'


def test_literal_ip_requests_use_the_same_address_policy_without_dns():
    api = SendAPIClient(transport_factory=lambda: httpx.MockTransport(lambda _: response()))
    assert api.send('GET', 'https://127.0.0.1', {}, None, 30).error == 'blocked_endpoint'
    assert api.send('GET', 'https://8.8.8.8', {}, None, 30).outcome == 'success'


def test_redirects_are_not_followed_and_server_cookies_are_not_reused():
    requests = []
    def handle(request):
        requests.append(request)
        return response(302, b'Redirect', 'text/plain', location='https://127.0.0.1/secret', **{'set-cookie': 'session=DO_NOT_FORWARD'})
    api = client(handle)
    assert api.send('GET', 'https://api.example.test', {}, None, 30).error == 'http_error'
    assert api.send('GET', 'https://api.example.test/second', {}, None, 30).error == 'http_error'
    assert len(requests) == 2 and all('cookie' not in request.headers for request in requests)


@pytest.mark.parametrize('exception,outcome,error', [(httpx.ConnectTimeout, 'timeout','timeout'), (httpx.ReadTimeout,'timeout','timeout'),
    (httpx.WriteTimeout,'timeout','timeout'), (httpx.ConnectError,'error','network_error'), (httpx.RemoteProtocolError,'error','network_error')])
def test_network_exceptions_are_controlled_without_echoing_endpoint_or_secrets(exception,outcome,error):
    def handle(_): raise exception('https://api.example.test?token=PRIVATE_TOKEN')
    result = client(handle).send('GET', 'https://api.example.test', {'Authorization':'Bearer PRIVATE_TOKEN'}, None, 30)
    assert result.outcome == outcome and result.error == error
    assert 'PRIVATE_TOKEN' not in json.dumps(result.variable())


@pytest.mark.parametrize('failure', [socket.gaierror, socket.timeout])
def test_dns_errors_and_timeouts_are_routed_without_sending_requests(failure):
    def resolve(*_): raise failure('Private DNS details')
    api = SendAPIClient(resolver=resolve)
    result = api.send('GET', 'https://api.example.test', {}, None, 30)
    assert result.error == ('timeout' if failure == socket.timeout else 'network_error')


@pytest.mark.parametrize('raw,headers,error', [
    (b'not JSON', {}, 'invalid_response'), (b'{"n":NaN}', {}, 'invalid_response'),
    (b'{"n":Infinity}', {}, 'invalid_response'), (b'{"x":' + b'['*64 + b'0' + b']'*64 + b'}', {}, 'response_too_large'),
    (b'x'*65537, {}, 'response_too_large'), (b'{"text":"' + b'x'*8193 + b'"}', {}, 'response_too_large'),
    (b'[]', {'content-length':'65537'}, 'response_too_large'),
    (gzip.compress(b'x'*1000000), {'content-encoding':'gzip'}, 'response_too_large'),
    (b'not gzip', {'content-encoding':'gzip'}, 'invalid_response'),
    (b'br data', {'content-encoding':'br'}, 'invalid_response')], ids=['malformed-json','nan','infinity','nesting','wire-size','text-size','content-length','gzip-limit','bad-gzip','unsupported-encoding'])
def test_malformed_or_oversized_responses_are_bounded(raw,headers,error):
    result = client(lambda _: response(body=raw, **headers)).send('GET', 'https://api.example.test', {}, None, 30)
    assert result.outcome == 'error' and result.error == error and result.body is None


def test_bounded_gzip_json_response_is_supported():
    result = client(lambda _: response(body=gzip.compress(b'{"status":"resolved"}'), **{'content-encoding':'gzip'})).send('GET', 'https://api.example.test', {}, None, 30)
    assert result.outcome == 'success' and result.body == {'status':'resolved'}


def test_oversized_chunked_body_stops_early_and_closes_response_stream():
    class Chunks(httpx.SyncByteStream):
        def __init__(self): self.count=0; self.closed=False
        def __iter__(self):
            for _ in range(100): self.count+=1; yield b'x'*16384
        def close(self): self.closed=True
    stream = Chunks()
    api = client(lambda _: httpx.Response(200, stream=stream))
    assert api.send('GET','https://api.example.test',{},None,30).error == 'response_too_large'
    assert stream.count == 5 and stream.closed


def test_slow_stream_routes_to_timeout_even_if_it_keeps_producing_chunks(monkeypatch):
    ticks = iter([0,0,0,31])
    monkeypatch.setattr('app.workflows.actions.send_api.client.time', SimpleNamespace(monotonic=lambda: next(ticks,31)))
    result = client(lambda _: response()).send('GET','https://api.example.test',{},None,30)
    assert result.outcome == 'timeout'


@pytest.fixture
def https_endpoint(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'api.example.test')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName('api.example.test')]),critical=False).sign(key,hashes.SHA256()))
    cert_path=tmp_path/'certificate.pem'; key_path=tmp_path/'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
    requests=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def handle_request(self):
            body=self.rfile.read(int(self.headers.get('content-length','0')))
            requests.append(dict(method=self.command,path=self.path,headers=dict(self.headers),body=body))
            if self.path=='/slow': time.sleep(1.3)
            raw=json.dumps({'method':self.command,'payload':json.loads(body) if body else None}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers()
            try:self.wfile.write(raw)
            except (BrokenPipeError,ssl.SSLError):pass
        do_GET=do_POST=do_PUT=do_PATCH=do_DELETE=handle_request
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(str(cert_path),str(key_path))
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    context=ssl.create_default_context(cafile=str(cert_path))
    api=SendAPIClient(allowed_private_hosts=['api.example.test'],resolver=lambda *_:['127.0.0.1'],ssl_context=context)
    yield api,'https://api.example.test:'+str(server.server_port),requests
    server.shutdown();server.server_close();thread.join(timeout=2)


def test_real_https_methods_tls_hostname_json_and_no_environment_proxy(https_endpoint,monkeypatch):
    api,url,requests=https_endpoint
    monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1')
    for method in ['GET','POST','PUT','PATCH','DELETE']:
        body=b'{"score":9,"active":true}' if method!='GET' else None
        result=api.send(method,url+'/accounts?reference=REF-123',{'Content-Type':'application/json','Authorization':'Bearer TEST_ONLY'},body,5)
        assert result.outcome=='success' and result.body['method']==method
        assert result.body['payload']==({'score':9,'active':True} if body else None)
    assert len(requests)==5 and all(request['path']=='/accounts?reference=REF-123' for request in requests)
    assert all(request['headers']['Host']==url.removeprefix('https://') for request in requests)


def test_real_https_certificate_verification_cannot_be_bypassed(https_endpoint):
    _,url,requests=https_endpoint
    untrusted=SendAPIClient(allowed_private_hosts=['api.example.test'],resolver=lambda *_:['127.0.0.1'])
    assert untrusted.send('GET',url,{},None,2).error=='network_error'
    assert requests==[]


def test_real_https_read_timeout_follows_timeout_path(https_endpoint):
    api,url,requests=https_endpoint
    result=api.send('GET',url+'/slow',{},None,1)
    assert result.outcome=='timeout' and result.error=='timeout' and len(requests)==1
