import { Routes } from '@angular/router';
import { pendingAgentChangesGuard, workspaceGuard } from './core/auth.guard';
import { settingsPages } from './features/settings/settings-navigation';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'signup' },
  { path: 'signup', loadComponent: () => import('./features/auth/auth-page.component').then(m => m.AuthPageComponent), data: { signup: true } },
  { path: 'login', loadComponent: () => import('./features/auth/auth-page.component').then(m => m.AuthPageComponent), data: { signup: false } },
  { path: 'auth/check-email', loadComponent: () => import('./features/auth/check-email.component').then(m => m.CheckEmailComponent) },
  { path: 'auth/verify', loadComponent: () => import('./features/auth/verify.component').then(m => m.VerifyComponent) },
  { path: 'auth/provider-email', loadComponent: () => import('./features/auth/provider-email.component').then(m => m.ProviderEmailComponent) },
  { path: 'onboarding/workspace', canActivate: [workspaceGuard], data: { onboarding: true }, loadComponent: () => import('./features/onboarding/workspace-setup.component').then(m => m.WorkspaceSetupComponent) },
  { path: 'app/agents', canActivate: [workspaceGuard], canDeactivate: [pendingAgentChangesGuard], loadComponent: () => import('./features/dashboard/dashboard.component').then(m => m.DashboardComponent) },
  { path: 'app/settings', pathMatch: 'full', redirectTo: 'app/settings/teamspace/database-connections' },
  ...settingsPages.map(page => ({ path: `app/settings/${page.key}`, canActivate: [workspaceGuard], canDeactivate: [pendingAgentChangesGuard], data: { settings: true, settingsKey: page.key }, loadComponent: () => import('./features/dashboard/dashboard.component').then(m => m.DashboardComponent) })),
  { path: 'unavailable', loadComponent: () => import('./features/auth/info-page.component').then(m => m.InfoPageComponent), data: { unavailable: true } },
  { path: 'terms', loadComponent: () => import('./features/auth/info-page.component').then(m => m.InfoPageComponent), data: { title: 'Terms of Use' } },
  { path: 'privacy', loadComponent: () => import('./features/auth/info-page.component').then(m => m.InfoPageComponent), data: { title: 'Privacy Policy' } },
  { path: '**', redirectTo: 'signup' }
];
