import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { ApiService, errorMessage } from '../../core/api.service';
import { AuthShellComponent } from '../../shared/auth-shell.component';
import { IconComponent } from '../../shared/icon.component';

@Component({ selector: 'petrix-provider-email', imports: [ReactiveFormsModule, RouterLink, AuthShellComponent, IconComponent], templateUrl: './provider-email.component.html' })
export class ProviderEmailComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly ticket = new URLSearchParams(window.location.hash.slice(1)).get('ticket');
  readonly busy = signal(false);
  readonly ready = signal(false);
  readonly error = signal('');
  readonly form = inject(FormBuilder).nonNullable.group({ email: ['', [Validators.required, Validators.email]] });
  constructor() { window.history.replaceState(null, '', window.location.pathname); void this.load(); }
  async load() {
    if (!this.ticket) { this.error.set('This sign-in request is incomplete. Please start again.'); return; }
    try {
      const info = await this.api.providerInfo(this.ticket);
      this.form.controls.email.setValue(info.email_hint || '');
      this.ready.set(true);
    } catch (error) { this.error.set(errorMessage(error)); }
  }
  async submit() {
    if (!this.ticket || !this.ready() || this.busy()) return;
    if (this.form.invalid) { this.form.markAllAsTouched(); return; }
    this.busy.set(true); this.error.set('');
    const email = this.form.controls.email.value.trim();
    try {
      await this.api.providerEmail(this.ticket, email);
      this.api.emailForInbox = email;
      await this.router.navigateByUrl('/auth/check-email');
    } catch (error) { this.error.set(errorMessage(error)); }
    finally { this.busy.set(false); }
  }
}
