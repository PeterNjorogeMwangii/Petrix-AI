import { Component, DestroyRef, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { interval } from 'rxjs';
import { ApiService, errorMessage } from '../../core/api.service';
import { AuthShellComponent } from '../../shared/auth-shell.component';
import { IconComponent } from '../../shared/icon.component';

@Component({ selector: 'petrix-check-email', imports: [RouterLink, AuthShellComponent, IconComponent], templateUrl: './check-email.component.html' })
export class CheckEmailComponent {
  private readonly api = inject(ApiService);
  readonly email = this.api.emailForInbox;
  readonly expiryMinutes = this.api.magicLinkMinutes;
  readonly countdown = signal(60);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly success = signal('');
  constructor() {
    interval(1000).pipe(takeUntilDestroyed(inject(DestroyRef))).subscribe(() => this.countdown.update(value => Math.max(0, value - 1)));
  }
  async resend() {
    if (!this.email || this.busy() || this.countdown()) return;
    this.busy.set(true); this.error.set(''); this.success.set('');
    try {
      await this.api.requestLink(this.email);
      this.countdown.set(60);
      this.success.set('A new sign-in link has been requested. Please check your inbox.');
    } catch (error) { this.error.set(errorMessage(error)); }
    finally { this.busy.set(false); }
  }
}
