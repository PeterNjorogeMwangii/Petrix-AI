"""A small email HTML allow-list; variable values are escaped before rendering."""
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit
import re

TAGS={'p','br','div','span','strong','b','em','i','u','ul','ol','li','h1','h2','h3','blockquote','hr','table','thead','tbody','tr','th','td','a'}
VOID={'br','hr'}
BLOCK={'p','div','h1','h2','h3','blockquote','li','tr','br','hr'}


class EmailHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html=[]; self.text=[]; self.stack=[]; self.hidden=0

    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','iframe','object','svg','math'}: self.hidden+=1; return
        if self.hidden or tag not in TAGS:return
        accepted=[]
        for key,value in attrs:
            if key=='href' and tag=='a' and value:
                try:parsed=urlsplit(value.strip())
                except ValueError:continue
                if parsed.scheme in {'https','mailto'} and not parsed.username and not any(ord(c)<32 for c in value):
                    accepted.append(('href',value));accepted.append(('rel','noopener noreferrer'))
            if key in {'colspan','rowspan'} and tag in {'td','th'} and value and re.fullmatch(r'[1-9][0-9]?',value):accepted.append((key,value))
        self.html.append('<'+tag+''.join(' {}="{}"'.format(k,escape(v,quote=True)) for k,v in accepted)+'>')
        if tag in BLOCK:self.text.append('\n')
        if tag not in VOID:self.stack.append(tag)

    def handle_endtag(self,tag):
        if tag in {'script','style','iframe','object','svg','math'}:
            self.hidden=max(0,self.hidden-1);return
        if self.hidden or tag not in self.stack:return
        while self.stack:
            current=self.stack.pop();self.html.append('</'+current+'>')
            if current==tag:break
        if tag in BLOCK:self.text.append('\n')

    def handle_data(self,data):
        if not self.hidden:self.html.append(escape(data));self.text.append(data)

    def finish(self):
        while self.stack:self.html.append('</'+self.stack.pop()+'>')
        return ''.join(self.html),re.sub(r'\n{3,}','\n\n',''.join(self.text)).strip()


def sanitize(value):
    parser=EmailHTML();parser.feed(value);parser.close();return parser.finish()
