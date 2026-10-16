import { Component, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { ApiService, errorMessage } from '../../core/api.service';
import { AuthShellComponent } from '../../shared/auth-shell.component';
import { IconComponent } from '../../shared/icon.component';

@Component({ selector: 'petrix-verify', imports: [RouterLink, AuthShellComponent, IconComponent], templateUrl: './verify.component.html' })
export class VerifyComponent {
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);
  private readonly token = new URLSearchParams(window.location.hash.slice(1)).get('token');
  readonly busy = signal(false);
  readonly error = signal(this.token ? '' : 'This link is missing its verification details. Please request a new one.');
  readonly available = signal(!!this.token);
  constructor() { window.history.replaceState(null, '', window.location.pathname); }
  async verify() {
    if (!this.token || this.busy()) return;
    this.busy.set(true); this.error.set('');
    try {
      const context = await this.api.verify(this.token);
      await this.router.navigateByUrl(context.next_path);
    } catch (error) { this.error.set(errorMessage(error)); }
    finally { this.busy.set(false); }
  }
}
