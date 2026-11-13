import { Component, Input } from '@angular/core';

@Component({
  selector: 'petrix-rating-face',
  template: `<svg viewBox="0 0 28 28" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="14" cy="14" r="10.5"/><circle cx="10" cy="11" r=".8" fill="currentColor"/><circle cx="18" cy="11" r=".8" fill="currentColor"/><path [attr.d]="mouth"/>@if (value===1) { <path d="m8 8 3 1m6 0 3-1"/> }@if (value===5) { <path d="M22 2v5m-2.5-2.5h5"/> }</svg>`,
  styles: [':host{display:inline-flex;width:28px;height:28px;flex-shrink:0}svg{width:100%;height:100%;overflow:visible}']
})
export class RatingFaceComponent {
  @Input() value = 3;
  get mouth() { return ['M8 20q6-8 12 0','M9 19q5-5 10 0','M9 18h10','M8 16q6 8 12 0','M8 16h12c-1 9-11 9-12 0Z'][this.value-1] || 'M9 18h10'; }
}
