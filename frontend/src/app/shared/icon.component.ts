import { Component, Input } from '@angular/core';

const paths: Record<string, string> = {
  spark: 'M12 3l2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3Z',
  variable: 'M8 3H6v6l-3 3 3 3v6h2M16 3h2v6l3 3-3 3v6h-2M10 12h4',
  jump: 'M4 19v-7a5 5 0 0 1 5-5h11M15 2l5 5-5 5',
  search: 'M15.5 15.5 21 21M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  mail: 'M3 5h18v14H3ZM3 6l9 7 9-7',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  check: 'M5 12l4 4L19 6',
  inbox: 'M4 4h16l2 12v4H2v-4ZM2 15h6l2 3h4l2-3h6',
  chat: 'M4 4h16v13H9l-5 4Z',
  ticket: 'M3 7h18v3a2 2 0 0 0 0 4v3H3v-3a2 2 0 0 0 0-4ZM15 7v2m0 2v2m0 2v2',
  people: 'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M15 4a4 4 0 0 1 0 8m7 9v-2a4 4 0 0 0-3-3M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  chart: 'M4 21V10h4v11ZM10 21V3h4v18ZM16 21V7h4v14Z',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Zm13 0a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  book: 'M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3Zm9 2v16',
  nodes: 'M9 3h6v6H9ZM2 15h6v6H2Zm14 0h6v6h-6ZM12 9v3M5 15v-3h14v3',
  globe: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3s4 4 4 9-4 9-4 9-4-4-4-9 4-9 4-9',
  chevron: 'M6 9l6 6 6-6',
  logout: 'M10 4H4v16h6m5-13 5 5-5 5m-7-5h12',
  upload: 'M12 16V3m-5 5 5-5 5 5M3 15v6h18v-6',
  lock: 'M6 10h12v11H6ZM8 10V7a4 4 0 0 1 8 0v3m-4 5v2',
  plus: 'M12 5v14M5 12h14',
  menu: 'M3 6h18M3 12h18M3 18h18',
  close: 'M6 6l12 12M18 6 6 18',
  refresh: 'M20 7v5h-5M4 17v-5h5M6 5a9 9 0 0 1 14 7M18 19a9 9 0 0 1-14-7',
  bolt: 'M13 2 4 14h7l-1 8 10-13h-7Z',
  paperclip: 'M9 15l6-6a3 3 0 0 0-4-4l-7 7a5 5 0 0 0 7 7l8-8',
  help: 'M9 8a3 3 0 1 1 4 3l-1 1v2m0 3h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  settings: 'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0',
  history: 'M3 11a9 9 0 1 1 2 7M3 4v7h7m2-4v6l4 2',
  download: 'M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5',
  trash: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7',
  voice: 'M9 4h6v10H9ZM5 11v3a7 7 0 0 0 14 0v-3M12 21v-4'
};

@Component({
  selector: 'petrix-icon',
  template: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path [attr.d]="path" /></svg>',
  styles: [':host { display:inline-flex; width:20px; height:20px; flex-shrink:0; } svg { width:100%; height:100%; }']
})
export class IconComponent {
  @Input() name = 'spark';
  get path() { return paths[this.name] || paths['spark']; }
}
