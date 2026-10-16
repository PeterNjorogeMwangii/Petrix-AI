import { Component, DestroyRef, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { ApiService, errorMessage } from '../../core/api.service';
import { OnboardingOptions } from '../../core/models';
import { BrandComponent } from '../../shared/brand.component';
import { IconComponent } from '../../shared/icon.component';

@Component({ selector: 'petrix-workspace-setup', imports: [ReactiveFormsModule, BrandComponent, IconComponent], templateUrl: './workspace-setup.component.html', styleUrl: './workspace-setup.component.css' })
export class WorkspaceSetupComponent {
  readonly api = inject(ApiService);
  private readonly router = inject(Router);
  readonly options = signal<OnboardingOptions | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly avatarPreview = signal('');
  private avatarFile: File | null = null;
  readonly form = inject(FormBuilder).nonNullable.group({
    name: ['', [Validators.required, Validators.maxLength(100), Validators.pattern(/\S/)]],
    industry: ['Other', Validators.required],
    website: ['', Validators.pattern(/^https?:\/\/\S+$/i)],
    country_code: ['', Validators.required]
  });

  constructor() {
    void this.loadOptions();
    inject(DestroyRef).onDestroy(() => { if (this.avatarPreview()) URL.revokeObjectURL(this.avatarPreview()); });
  }
  get initial() { return (this.form.controls.name.value.trim()[0] || 'P').toUpperCase(); }
  async loadOptions() {
    this.error.set('');
    try {
      const options = await this.api.onboardingOptions();
      this.options.set(options);
      this.form.controls.country_code.setValue(options.default_country || '');
    } catch (error) { this.error.set(errorMessage(error)); }
  }
  chooseAvatar(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    if (file.size > 2 * 1024 * 1024 || !['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) {
      this.error.set('Choose a PNG, JPEG, or WebP image smaller than 2 MB.');
      input.value = ''; return;
    }
    if (this.avatarPreview()) URL.revokeObjectURL(this.avatarPreview());
    this.avatarFile = file;
    this.avatarPreview.set(URL.createObjectURL(file));
    this.error.set('');
  }
  removeAvatar() {
    if (this.avatarPreview()) URL.revokeObjectURL(this.avatarPreview());
    this.avatarPreview.set(''); this.avatarFile = null;
  }
  async submit() {
    if (this.form.invalid) { this.form.markAllAsTouched(); return; }
    const context = this.api.context();
    if (!context || !this.options() || this.busy()) return;
    this.busy.set(true); this.error.set('');
    const values = this.form.getRawValue();
    const body = new FormData();
    body.append('name', values.name.trim());
    body.append('industry', values.industry);
    body.append('country_code', values.country_code);
    body.append('website', values.website.trim());
    if (this.avatarFile) body.append('avatar', this.avatarFile);
    try {
      const result = await this.api.setupWorkspace(context.workspace.id, body);
      await this.router.navigateByUrl(result.next_path);
    } catch (error) { this.error.set(errorMessage(error)); }
    finally { this.busy.set(false); }
  }
  async signOut() {
    try { await this.api.logout(); await this.router.navigateByUrl('/login'); }
    catch (error) { this.error.set(errorMessage(error)); }
  }
}
