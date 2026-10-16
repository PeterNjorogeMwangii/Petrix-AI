import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { ApiService, errorMessage } from '../../core/api.service';
import { AuthOptions } from '../../core/models';
import { AuthShellComponent } from '../../shared/auth-shell.component';
import { IconComponent } from '../../shared/icon.component';

@Component({ selector: 'petrix-auth-page', imports: [ReactiveFormsModule, RouterLink, AuthShellComponent, IconComponent], templateUrl: './auth-page.component.html' })
export class AuthPageComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);
  readonly isSignup = this.route.snapshot.data['signup'] === true;
  readonly options = signal<AuthOptions | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly form = inject(FormBuilder).nonNullable.group({ email: ['', [Validators.required, Validators.email]] });

  constructor() {
    const authError = this.route.snapshot.queryParamMap.get('error');
    if (authError === 'provider_failed') this.error.set('We could not complete that sign-in. Try again or continue with email.');
    if (authError === 'provider_unavailable') this.error.set('That sign-in method is currently unavailable. Continue with email.');
    void this.loadOptions();
  }

  async loadOptions() {
    try { this.options.set(await this.api.authOptions()); }
    catch (error) { this.error.set(errorMessage(error)); }
  }
  async submit() {
    if (this.form.invalid) { this.form.markAllAsTouched(); return; }
    if (this.busy()) return;
    this.busy.set(true);
    this.error.set('');
    const email = this.form.controls.email.value.trim();
    try {
      await this.api.requestLink(email);
      this.api.emailForInbox = email;
      await this.router.navigateByUrl('/auth/check-email');
    } catch (error) { this.error.set(errorMessage(error)); }
    finally { this.busy.set(false); }
  }
  oauth(provider: 'google' | 'microsoft') { window.location.assign(`/api/auth/oauth/${provider}/start`); }
}
