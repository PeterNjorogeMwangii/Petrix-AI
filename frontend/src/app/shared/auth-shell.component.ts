import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BrandComponent } from './brand.component';
import { IconComponent } from './icon.component';

@Component({ selector: 'petrix-auth-shell', imports: [BrandComponent, IconComponent, RouterLink], templateUrl: './auth-shell.component.html' })
export class AuthShellComponent {}
