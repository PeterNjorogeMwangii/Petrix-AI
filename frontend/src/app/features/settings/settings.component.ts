import { Component, Input, ViewChild, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { IconComponent } from '../../shared/icon.component';
import { DatabaseConnectionsComponent } from './database-connections.component';
import { settingsGroups, settingsPages } from './settings-navigation';

@Component({
  selector: 'petrix-settings', imports: [FormsModule, RouterLink, IconComponent, DatabaseConnectionsComponent],
  templateUrl: './settings.component.html', styleUrl: './settings.component.css'
})
export class SettingsComponent {
  @Input({ required: true }) section = '';
  @ViewChild(DatabaseConnectionsComponent) database?: DatabaseConnectionsComponent;
  private readonly router = inject(Router);
  readonly groups = settingsGroups;
  page() { return settingsPages.find(page => page.key === this.section)!; }
  dirty() { return this.database?.dirty() || false; }
  canLeave() { return this.database?.canLeave() ?? true; }
  navigate(key: string) { void this.router.navigateByUrl(`/app/settings/${key}`); }
}
