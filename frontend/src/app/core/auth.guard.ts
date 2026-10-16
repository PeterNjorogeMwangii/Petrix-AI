import { inject } from '@angular/core';
import { CanActivateFn, CanDeactivateFn, Router } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { ApiService } from './api.service';

export const workspaceGuard: CanActivateFn = async route => {
  const api = inject(ApiService);
  const router = inject(Router);
  try {
    const context = await api.loadContext();
    const onboarding = route.data['onboarding'] === true;
    if (!context.workspace.setup_completed && !onboarding) return router.parseUrl('/onboarding/workspace');
    if (context.workspace.setup_completed && onboarding) return router.parseUrl('/app/agents');
    return true;
  } catch (error) {
    return router.parseUrl(error instanceof HttpErrorResponse && error.status === 401 ? '/login' : '/unavailable');
  }
};

export const pendingAgentChangesGuard: CanDeactivateFn<{ canLeave(): boolean }> = component => component.canLeave();
