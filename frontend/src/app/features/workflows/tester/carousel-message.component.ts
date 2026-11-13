import { AfterViewInit, Component, ElementRef, EventEmitter, Input, OnChanges, OnDestroy, Output, ViewChild, signal } from '@angular/core';
import { IconComponent } from '../../../shared/icon.component';
import { CarouselButton, WorkflowCarousel } from '../workflow.models';

@Component({
  selector: 'petrix-carousel-message', imports: [IconComponent],
  template: `
    <div class="carousel-controls"><span>Options</span><div><button type="button" aria-label="Previous carousel cards" (click)="scroll(-1)" [disabled]="!canPrevious()">‹</button><button type="button" aria-label="Next carousel cards" (click)="scroll(1)" [disabled]="!canNext()">›</button></div></div>
    <div #rail class="carousel-rail" role="region" aria-label="Carousel cards" tabindex="0" (scroll)="measure()" (keydown.arrowleft)="scroll(-1);$event.preventDefault()" (keydown.arrowright)="scroll(1);$event.preventDefault()">
      @for (card of carousel.cards; track card.id) {
        <article class="carousel-card" [attr.aria-label]="card.title">
          @if (card.image && !failedImages().has(card.id)) { <img [src]="card.image.external ? card.image.url : card.image.url + '?inline=true'" [alt]="card.title" loading="lazy" (load)="measure()" (error)="imageError(card.id)"> }
          <div class="card-text"><h4>{{ card.title }}</h4>@if (card.subtitle) { <p>{{ card.subtitle }}</p> }</div>
          @if (card.buttons.length) { <div class="card-buttons">@for (button of card.buttons; track button.id) {
            @if (button.type === 'reply') { <button type="button" [disabled]="disabled" (click)="reply.emit(button.message)">{{ button.label }}</button> }
            @else if (button.webview) { <button type="button" (click)="webview.emit({button,opener:$event.currentTarget})">{{ button.label }}<petrix-icon name="globe" /></button> }
            @else { <a [href]="button.url" target="_blank" rel="noopener noreferrer">{{ button.label }}<petrix-icon name="arrow" /></a> }
          }</div> }
        </article>
      }
    </div>
  `,
  styles: [`
    :host{display:block;min-width:0;max-width:100%}.carousel-controls{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:8px;color:var(--muted);font-size:10px}.carousel-controls>div{display:flex;gap:5px}.carousel-controls button{background:#ffffff05;border:1px solid #55555c;border-radius:5px;color:var(--text);width:26px;height:24px;font-size:20px;line-height:1}.carousel-controls button:disabled{opacity:.25}
    .carousel-rail{display:flex;gap:10px;overflow-x:auto;scroll-snap-type:x mandatory;overscroll-behavior-x:contain;padding-bottom:10px;max-width:100%;scrollbar-width:thin;scrollbar-color:#66666e transparent}.carousel-card{display:flex;flex-direction:column;flex:0 0 90%;min-width:0;scroll-snap-align:start;border:1px solid #55555c;background:#222226;border-radius:9px;overflow:hidden}.carousel-card img{display:block;width:100%;height:142px;object-fit:cover;background:#151517}.card-text{padding:13px;flex:1}.card-text h4{font-size:13px;line-height:1.5;font-weight:600;margin:0;color:var(--text);overflow-wrap:anywhere}.card-text p{font-size:11px;line-height:1.6;color:#b6b6be;margin:7px 0 0;white-space:pre-wrap;overflow-wrap:anywhere}.card-buttons{display:flex;flex-direction:column}.card-buttons button,.card-buttons a{display:flex;align-items:center;justify-content:center;gap:7px;padding:11px 9px;background:#ffffff03;border:0;border-top:1px solid #48484e;color:#f6bd91;font-size:11px;font-weight:500;text-align:center;text-decoration:none;overflow-wrap:anywhere;line-height:1.5}.card-buttons button:disabled{opacity:.38}.card-buttons button:not(:disabled):hover,.card-buttons a:hover{background:#ffffff09}.card-buttons petrix-icon{width:13px;height:13px;flex:none}button:not(:disabled){cursor:pointer}button:focus-visible,a:focus-visible,.carousel-rail:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}@media(prefers-reduced-motion:reduce){.carousel-rail{scroll-behavior:auto}}
  `]
})
export class CarouselMessageComponent implements OnChanges, AfterViewInit, OnDestroy {
  @Input({ required: true }) carousel!: WorkflowCarousel;
  @Input() disabled = false;
  @Output() reply = new EventEmitter<string>();
  @Output() webview = new EventEmitter<{ button: CarouselButton; opener: EventTarget | null }>();
  @ViewChild('rail') rail?: ElementRef<HTMLElement>;
  readonly failedImages = signal(new Set<string>());
  readonly canPrevious = signal(false);
  readonly canNext = signal(false);
  private observer?: ResizeObserver;
  ngAfterViewInit() { this.observer = new ResizeObserver(() => this.measure()); if (this.rail) this.observer.observe(this.rail.nativeElement); this.measure(); }
  ngOnDestroy() { this.observer?.disconnect(); }
  ngOnChanges() { setTimeout(() => this.measure(), 0); }
  measure() { const rail = this.rail?.nativeElement; if (rail) { this.canPrevious.set(rail.scrollLeft > 2); this.canNext.set(rail.scrollLeft + rail.clientWidth < rail.scrollWidth - 2); } }
  scroll(direction: number) {
    const rail = this.rail?.nativeElement; if (!rail) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    rail.scrollBy({ left: direction * rail.clientWidth * .95, behavior: reduced ? 'instant' : 'smooth' });
  }
  imageError(id: string) { this.failedImages.update(ids => new Set([...ids, id])); }
}
