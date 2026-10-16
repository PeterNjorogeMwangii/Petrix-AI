import { Component } from '@angular/core';

@Component({
  selector: 'petrix-brand',
  template: '<span class="brand-mark" aria-hidden="true"><svg viewBox="0 0 40 40"><path d="M12 29V11h10a7 7 0 0 1 0 14h-5v4zm5-9h5a2 2 0 0 0 0-4h-5z" fill="currentColor"/></svg></span><span class="brand-name">petrix<span class="brand-ai">AI</span></span>',
  styles: [':host { display:inline-flex; align-items:center; gap:10px; } .brand-mark { width:36px; height:36px; background:var(--accent); border-radius:11px; color:#231810; display:flex; } svg { width:100%; } .brand-name { font-weight:750; font-size:26px; letter-spacing:-1.1px; } .brand-ai { font-size:11px; letter-spacing:0.5px; color:var(--accent); border:1px solid #ff8b453d; padding:3px 5px; border-radius:5px; margin-left:8px; vertical-align:middle; }']
})
export class BrandComponent {}
