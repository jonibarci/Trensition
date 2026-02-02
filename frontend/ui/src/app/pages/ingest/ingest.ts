import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  OnInit,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { finalize } from 'rxjs';

import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ApiService, CookieStatus, IngestResponse } from '../../services/api.service';

@Component({
  selector: 'app-ingest',
  imports: [CommonModule, FormsModule],
  templateUrl: './ingest.html',
  styleUrl: './ingest.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Ingest implements OnInit {
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly company = signal('');
  protected readonly limit = signal<number | null>(5);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly response = signal<IngestResponse | null>(null);
  protected readonly cookieStatus = signal<CookieStatus | null>(null);
  protected readonly cookieLoading = signal(false);
  protected readonly cookieRefreshing = signal(false);
  protected readonly cookieDeleting = signal(false);

  get companyValue(): string {
    return this.company();
  }

  set companyValue(value: string) {
    this.company.set(value);
  }

  get limitValue(): number | null {
    return this.limit();
  }

  set limitValue(value: number | null) {
    this.limit.set(value);
  }

  ngOnInit(): void {
    this.checkCookieStatus();
  }

  checkCookieStatus(): void {
    this.cookieLoading.set(true);
    this.api
      .getCookieStatus()
      .pipe(
        finalize(() => this.cookieLoading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (status) => this.cookieStatus.set(status),
        error: () => this.cookieStatus.set({ valid: false, message: 'Failed to check cookie status', cookie_file_exists: false }),
      });
  }

  refreshCookies(): void {
    this.cookieRefreshing.set(true);
    this.api
      .refreshCookies()
      .pipe(
        finalize(() => {
          this.cookieRefreshing.set(false);
          // Re-check status after refresh
          this.checkCookieStatus();
        }),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: () => {},
        error: () => this.error.set('Failed to refresh cookies. Please try again.'),
      });
  }

  deleteCookies(): void {
    this.cookieDeleting.set(true);
    this.api
      .deleteCookies()
      .pipe(
        finalize(() => {
          this.cookieDeleting.set(false);
          // Re-check status after delete
          this.checkCookieStatus();
        }),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: () => {},
        error: () => this.error.set('Failed to delete cookies. Please try again.'),
      });
  }

  ingest(): void {
    const company = this.company().trim();
    if (!company) {
      this.error.set('Company is required.');
      return;
    }

    this.loading.set(true);
    this.error.set(null);
    this.response.set(null);

    const limit = this.limit();
    this.api
      .ingestCompany(company, limit !== null ? limit : undefined)
      .pipe(
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (response) => this.response.set(response),
        error: () => this.error.set('Ingest failed. Please try again.'),
      });
  }
}
