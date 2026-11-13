import { Component, EventEmitter, Input, OnChanges, Output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { IconComponent } from '../../../../shared/icon.component';
import { CarouselCardConfig, CarouselImage, ShowCarouselConfig } from '../../workflow.models';
import { CarouselImageEditorComponent } from './carousel-image-editor.component';
import { carouselConfigError, newCarouselButton, newCarouselCard } from './carousel-config';

@Component({
  selector: 'petrix-show-carousel-editor', imports: [FormsModule, IconComponent, CarouselImageEditorComponent],
  templateUrl: './show-carousel-editor.component.html', styleUrl: './show-carousel-editor.component.css'
})
export class ShowCarouselEditorComponent implements OnChanges {
  @Input({ required: true }) config!: ShowCarouselConfig;
  @Input({ required: true }) agentId!: string;
  @Input() language = 'en';
  @Input() disabled = false;
  @Output() configChange = new EventEmitter<ShowCarouselConfig>();
  @Output() uploadBusyChange = new EventEmitter<boolean>();
  @Output() assetUploaded = new EventEmitter<string>();
  cards: CarouselCardConfig[] = [];
  readonly expanded = signal<string | null>(null);
  hasTranslation = false;
  private lastLanguage = ''; private draggedId: string | null = null;
  ngOnChanges() {
    this.hasTranslation = !!this.config.translations[this.language];
    this.cards = structuredClone(this.config.translations[this.language]?.cards || []);
    if (this.lastLanguage !== this.language) { this.expanded.set(null); this.lastLanguage = this.language; }
  }
  update() { this.configChange.emit({ translations: { ...this.config.translations, [this.language]: { cards: structuredClone(this.cards) } } }); }
  error() { return carouselConfigError(this.config); }
  toggle(id: string) { this.expanded.update(value => value === id ? null : id); }
  addCard() { if (this.disabled || this.cards.length >= 10) return; const card = newCarouselCard(); this.cards.push(card); this.expanded.set(card.id); this.update(); }
  removeCard(index: number) { this.cards.splice(index, 1); this.update(); }
  moveCard(index: number, direction: number) { this.move(index, index + direction); }
  private move(from: number, to: number) {
    if (this.disabled || from < 0 || to < 0 || from === to || to >= this.cards.length) return;
    const [card] = this.cards.splice(from, 1); this.cards.splice(to, 0, card); this.update();
  }
  dragStart(event: DragEvent, card: CarouselCardConfig) {
    if (this.disabled) { event.preventDefault(); return; }
    this.draggedId = card.id; if (event.dataTransfer) { event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', card.id); }
  }
  dragOver(event: DragEvent) { if (this.draggedId) event.preventDefault(); }
  drop(event: DragEvent, index: number) { if (!this.draggedId) return; event.preventDefault(); this.move(this.cards.findIndex(card => card.id === this.draggedId), index); this.draggedId = null; }
  dragEnd() { this.draggedId = null; }
  setImage(index: number, image: CarouselImage | null) { this.cards[index].image = image; this.update(); }
  addButton(index: number) { if (this.cards[index].buttons.length < 3) { this.cards[index].buttons.push(newCarouselButton()); this.update(); } }
  removeButton(card: number, index: number) { this.cards[card].buttons.splice(index, 1); this.update(); }
  changeButtonType(card: number, index: number) { const button = this.cards[card].buttons[index]; button.message = ''; button.url = ''; button.webview = false; this.update(); }
}
