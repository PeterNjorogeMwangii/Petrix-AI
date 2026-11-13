import { Component, EventEmitter, HostListener, Output, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ApiService } from '../core/api.service';
import { IconComponent } from './icon.component';

@Component({
  selector: 'petrix-profile-menu', imports: [RouterLink, IconComponent],
  template: `<button class="profile-toggle" aria-label="Open profile menu" aria-haspopup="menu" [attr.aria-expanded]="open()" (click)="open.set(!open())"><span></span><petrix-icon name="chevron" /></button>
  @if (open()) { <div class="profile-popover" role="menu" aria-label="Profile"><header><strong>{{ api.context()?.user?.full_name || 'Your account' }}</strong><span>{{ api.context()?.user?.email }}</span></header><section><span class="label">Set as <small>Planned</small></span><div class="choices"><button disabled>Online</button><button disabled>Away</button><button disabled>Offline</button></div></section><section><span class="label">Theme <small>Planned</small></span><div class="choices"><button disabled>System</button><button disabled>Light</button><button disabled>Dark</button></div></section><a role="menuitem" routerLink="/app/settings/teamspace/database-connections" (click)="open.set(false)"><petrix-icon name="settings" />Settings</a><button class="menu-action" role="menuitem" disabled><petrix-icon name="people" />Change Workspace <small>Planned</small></button><button class="menu-action sign-out" role="menuitem" (click)="open.set(false); signOut.emit()"><petrix-icon name="logout" />Sign Out</button></div> }`,
  styles: [`:host{display:block;position:relative}.profile-toggle{display:flex;align-items:center;gap:5px;background:transparent;border:0;color:#99909d;padding:7px}.profile-toggle>span{width:6px;height:6px;border-radius:50%;background:#a1b59a}.profile-toggle petrix-icon{width:13px;height:13px}.profile-popover{position:absolute;z-index:45;bottom:45px;left:-195px;width:255px;max-width:calc(100vw - 35px);background:#27252a;border:1px solid #4a414d;border-radius:10px;padding:7px;box-shadow:0 15px 60px #0008}header{padding:12px;border-bottom:1px solid #3d3641;overflow-wrap:anywhere}header strong{display:block;color:#ded6df;font-size:12px;font-weight:500}header>span{display:block;font-size:10px;color:#9b909f;margin-top:6px}section{padding:12px;border-bottom:1px solid #3d3641}.label{display:flex;justify-content:space-between;font-size:10px;color:#c3b9c8;margin-bottom:10px}small{font-size:8px;color:#8e7b98}.choices{display:flex;gap:5px}.choices button{flex:1;color:#aaa0b0;background:#342f39;border:1px solid #494050;padding:6px 5px;font-size:9px;border-radius:5px;opacity:.55}a,.menu-action{display:flex;align-items:center;gap:10px;font-family:inherit;font-size:11px;text-decoration:none;padding:11px;color:#c8bdce;width:100%;border:0;border-radius:5px;background:transparent;text-align:left}a:hover,.menu-action:hover:not(:disabled){background:#ffffff06}.menu-action:disabled{opacity:.5}.menu-action small{margin-left:auto}.profile-popover petrix-icon{width:14px;height:14px}.sign-out{color:#d8aba7}`]
})
export class ProfileMenuComponent {
  readonly api = inject(ApiService);
  readonly open = signal(false);
  @Output() signOut = new EventEmitter<void>();
  @HostListener('document:click', ['$event']) outside(event: MouseEvent) { if (!(event.target instanceof Element && event.target.closest('petrix-profile-menu'))) this.open.set(false); }
  @HostListener('document:keydown.escape') escape() { this.open.set(false); }
}
