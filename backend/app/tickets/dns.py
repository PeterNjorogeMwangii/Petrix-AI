"""Bounded DNS ownership checks using the configured system resolver."""
import dns.resolver
import dns.exception


class DomainDNS:
    def records(self,name,kind):
        try:
            values=dns.resolver.resolve(name,kind,lifetime=4,search=False)
            if kind=='TXT':return [b''.join(item.strings).decode('utf-8','replace') for item in values]
            return [item.target.to_text().rstrip('.').lower() for item in values]
        except (dns.exception.DNSException,UnicodeError):return []
