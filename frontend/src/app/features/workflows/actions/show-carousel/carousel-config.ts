import { CarouselButton, CarouselCardConfig, ShowCarouselConfig } from '../../workflow.models';

export function newCarouselCard(): CarouselCardConfig {
  return { id: crypto.randomUUID(), title: '', subtitle: '', image: null, buttons: [] };
}
export function newCarouselButton(): CarouselButton {
  return { id: crypto.randomUUID(), label: '', type: 'reply', message: '', url: '', webview: false };
}
export function validCarouselLink(value: string) {
  try {
    const url = new URL(value);
    return value.length <= 2048 && /^https?:\/\//i.test(value) && !!url.hostname && !url.username && !url.password && !/[\s\\{}\x00-\x1f\x7f]/.test(value);
  } catch { return false; }
}
export function carouselConfigError(config: ShowCarouselConfig) {
  if (!Object.keys(config.translations).length) return 'Add at least one card.';
  for (const [language, translation] of Object.entries(config.translations)) {
    if (!translation.cards.length || translation.cards.length > 10) return `${language}: add 1–10 cards.`;
    for (const [index, card] of translation.cards.entries()) {
      const prefix = `${language}, card ${index + 1}: `;
      if (!card.title.trim() || card.title.length > 80) return prefix + 'enter a title of up to 80 characters.';
      if (card.subtitle.length > 200) return prefix + 'keep the subtitle within 200 characters.';
      if (card.image?.source === 'upload' && !card.image.asset_id) return prefix + 'upload an image or choose No image.';
      if (card.image?.source === 'link' && !validCarouselLink(card.image.url)) return prefix + 'enter a complete HTTP or HTTPS image URL.';
      if (card.buttons.length > 3) return prefix + 'use up to 3 buttons.';
      for (const button of card.buttons) {
        if (!button.label.trim() || button.label.length > 80) return prefix + 'enter every button label (up to 80 characters).';
        if (button.type === 'reply' && (!button.message.trim() || button.message.length > 1000)) return prefix + 'enter the message sent by each reply button (up to 1,000 characters).';
        if (button.type === 'link' && !validCarouselLink(button.url)) return prefix + 'enter a complete HTTP or HTTPS webpage URL.';
      }
    }
  }
  return '';
}
