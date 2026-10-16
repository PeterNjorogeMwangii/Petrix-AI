import { Component, inject } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { AuthShellComponent } from '../../shared/auth-shell.component';
import { IconComponent } from '../../shared/icon.component';

@Component({
  selector: 'petrix-info-page', imports: [RouterLink, AuthShellComponent, IconComponent],
  template: `<petrix-auth-shell><div class="auth-status-icon"><petrix-icon [name]="unavailable ? 'refresh' : 'book'" /></div><div class="eyebrow">Petrix AI</div><h1>{{ title }}</h1><p class="auth-subtitle">{{ description }}</p>@if (unavailable) { <a class="btn btn-primary btn-full" routerLink="/app/agents">Try again</a><a class="back-link" routerLink="/login">Return to sign in</a> } @else { <div class="notice">This is a development preview. Final legal documents will be added before public launch.</div><a class="back-link" routerLink="/signup">Return to Petrix AI</a> }</petrix-auth-shell>`
})
export class InfoPageComponent {
  private readonly route = inject(ActivatedRoute);
  readonly unavailable = this.route.snapshot.data['unavailable'] === true;
  readonly title = this.unavailable ? 'We cannot load your workspace' : this.route.snapshot.data['title'];
  readonly description = this.unavailable ? 'Petrix AI is temporarily unavailable. Please check your connection and try again.' : 'We are preparing the terms and privacy information for Petrix AI.';
}
