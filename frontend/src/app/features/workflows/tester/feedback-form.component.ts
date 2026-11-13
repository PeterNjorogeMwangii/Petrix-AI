import { Component, EventEmitter, Input, OnChanges, Output } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { FeedbackRequest } from '../workflow.models';
import { RatingFaceComponent } from '../../../shared/rating-face.component';

export interface FeedbackSubmission { rating?: number; answers: Record<string,string> }

@Component({
  selector: 'petrix-feedback-form', imports: [FormsModule, RatingFaceComponent],
  template: `
    <form class="feedback-request" aria-label="Customer feedback" (ngSubmit)="submit()">
      <fieldset [disabled]="disabled || busy">
        @if (feedback.rating_enabled) {
          <legend>{{ feedback.channel==='webchat' ? feedback.title : feedback.request_message }}</legend>
          @if (feedback.typed_rating) {
            <label class="typed-label" for="typed-feedback-rating">Type a rating from 1 to 5</label><input id="typed-feedback-rating" type="number" min="1" max="5" step="1" [ngModelOptions]="{standalone:true}" [(ngModel)]="rating">
          } @else {
            <div class="rating-options" [class.numeric]="feedback.channel!=='webchat'">
              @for (value of [1,2,3,4,5]; track value) { <label class="rating-option" [class.selected]="rating===value"><input type="radio" name="feedbackRating" [value]="value" [ngModel]="rating" (ngModelChange)="rating=$event" [ngModelOptions]="{standalone:true}" [attr.aria-label]="'Rate conversation '+value+' out of 5'"><span aria-hidden="true">@if (feedback.channel==='webchat') { <petrix-rating-face [value]="value" /> } @else { {{ value }} }</span></label> }
            </div><p class="scale-note">1 = Very poor · 5 = Excellent</p>
          }
        } @else { <legend>Share your feedback</legend> }
        @for (field of feedback.fields; track field.id) {
          <div class="feedback-field"><label [for]="'feedback-answer-'+field.id">{{ field.question }}<small>{{ field.required ? 'Required' : 'Optional' }}</small></label>
            @if (field.reply_type==='choice') { <select [id]="'feedback-answer-'+field.id" [ngModelOptions]="{standalone:true}" [ngModel]="answers[field.id] || ''" (ngModelChange)="answer(field.id,$event)"><option value="">{{ field.placeholder || 'Select an answer' }}</option>@for (choice of field.choices; track choice) { <option [value]="choice">{{ choice }}</option> }</select> }
            @else if (field.reply_type==='open_response') { <textarea [id]="'feedback-answer-'+field.id" [placeholder]="field.placeholder" [ngModelOptions]="{standalone:true}" [ngModel]="answers[field.id] || ''" (ngModelChange)="answer(field.id,$event)" maxlength="4000" rows="3"></textarea> }
            @else { <input [id]="'feedback-answer-'+field.id" [type]="field.reply_type==='email' ? 'email' : field.reply_type==='phone' ? 'tel' : 'text'" [attr.inputmode]="field.reply_type==='number' ? 'decimal' : null" [placeholder]="field.placeholder" [ngModelOptions]="{standalone:true}" [ngModel]="answers[field.id] || ''" (ngModelChange)="answer(field.id,$event)" maxlength="4000"> }
          </div>
        }
        <button class="btn btn-primary submit-feedback" type="submit" [disabled]="disabled || busy || !ready()">{{ busy ? 'Saving…' : feedback.fields.length ? 'Submit feedback' : 'Submit rating' }}</button>
      </fieldset>
    </form>
  `,
  styles: [`:host{display:block;max-height:48vh;overflow:auto;flex-shrink:0;scrollbar-width:thin;margin-top:10px}.feedback-request{border:1px solid #66503b;border-radius:9px;background:#ff8b4508;padding:15px 12px}fieldset{border:0;padding:0;margin:0;min-width:0}legend{font-size:12px;font-weight:500;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;padding:0;margin-bottom:12px;max-width:100%}.rating-options{display:flex;justify-content:space-around;gap:4px}.rating-option{position:relative;width:40px;height:43px;display:flex;align-items:center;justify-content:center;border:1px solid transparent;border-radius:7px;cursor:pointer}.rating-option input{position:absolute;inset:0;width:100%;height:100%;margin:0;opacity:0;cursor:pointer}.rating-option span{color:#bbb5bd;display:flex;font-size:27px;line-height:1;pointer-events:none;filter:grayscale(1);opacity:.65}.rating-option.selected span{color:#ffb574;filter:none;opacity:1}.rating-option:has(input:focus-visible){outline:2px solid var(--accent);outline-offset:1px}.rating-option.selected{border-color:#b67e4d;background:#ff8b4518}.numeric .rating-option{border-color:#72503a}.numeric .rating-option span{font-size:14px;color:var(--text);filter:none;opacity:1}.scale-note{font-size:9px;color:var(--muted);text-align:center;margin:9px 0 15px}.feedback-field{margin:17px 0}.feedback-field label,.typed-label{display:block;font-size:11px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;margin-bottom:8px}.feedback-field small{display:block;color:var(--muted);font-size:9px}input,textarea,select{width:100%;min-width:0;max-width:100%;border:1px solid #44444c;border-radius:6px;background:#1b1b1f;color:var(--text);padding:10px;font-size:11px}textarea{resize:vertical;line-height:1.5}input:focus-visible,textarea:focus-visible,select:focus-visible{outline:2px solid var(--accent);outline-offset:1px}.submit-feedback{width:100%;font-size:11px;min-height:35px;padding:8px;margin-top:8px}fieldset:disabled{opacity:.6}`]
})
export class FeedbackFormComponent implements OnChanges {
  @Input({required:true}) feedback!: FeedbackRequest;
  @Input() busy = false;
  @Input() disabled = false;
  @Output() submitted = new EventEmitter<FeedbackSubmission>();
  rating: number | null = null; answers: Record<string,string> = {}; private token = '';
  ngOnChanges() { if (this.token!==this.feedback.token) { this.token=this.feedback.token;this.rating=null;this.answers={}; } }
  answer(id: string, value: unknown) { this.answers[id]=String(value ?? ''); }
  ready() {
    if (this.feedback.rating_enabled && (this.rating===null || !Number.isInteger(this.rating) || this.rating<1 || this.rating>5)) return false;
    return this.feedback.fields.every(field=>!field.required || !!this.answers[field.id]?.trim());
  }
  submit() { if (!this.disabled && !this.busy && this.ready()) this.submitted.emit({rating:this.feedback.rating_enabled ? this.rating! : undefined,answers:{...this.answers}}); }
}
