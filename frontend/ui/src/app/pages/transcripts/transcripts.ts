import { CommonModule } from '@angular/common';
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { finalize } from 'rxjs';

import { ApiService, TranscriptListItem } from '../../services/api.service';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';

@Component({
  selector: 'app-transcripts',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './transcripts.html',
  styleUrl: './transcripts.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Transcripts {
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly company = signal('');
  protected readonly query = signal('');
  protected readonly transcripts = signal<TranscriptListItem[]>([]);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly hasSearched = signal(false);
  protected readonly hasResults = computed(() => this.transcripts().length > 0);

  get companyValue(): string {
    return this.company();
  }

  set companyValue(value: string) {
    this.company.set(value);
  }

  get queryValue(): string {
    return this.query();
  }

  set queryValue(value: string) {
    this.query.set(value);
  }

  search(): void {
    this.loading.set(true);
    this.error.set(null);
    this.hasSearched.set(true);

    this.api
      .listTranscripts({
        company: this.company().trim() || undefined,
        q: this.query().trim() || undefined,
        limit: 50,
        offset: 0,
      })
      .pipe(
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (items) => this.transcripts.set(items),
        error: () => {
          this.error.set('Failed to load transcripts. Try again.');
          this.transcripts.set([]);
        },
      });
  }
}
