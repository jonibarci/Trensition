import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  computed,
  inject,
  signal,
} from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { catchError, filter, finalize, map, of, switchMap, tap } from 'rxjs';

import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ApiService, TranscriptDetail as TranscriptDetailModel } from '../../services/api.service';

@Component({
  selector: 'app-transcript-detail',
  imports: [CommonModule],
  templateUrl: './transcript-detail.html',
  styleUrl: './transcript-detail.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class TranscriptDetail {
  private readonly api = inject(ApiService);
  private readonly route = inject(ActivatedRoute);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly transcript = signal<TranscriptDetailModel | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly selectedOrg = signal<string | null>(null);
  protected readonly searchQuery = signal<string | null>(null);
  protected readonly highlightedTurns = signal<Set<number>>(new Set());
  protected readonly hasOrganizations = computed(
    () => (this.transcript()?.organizations.length ?? 0) > 0
  );

  ngOnInit(): void {
    // Read search query and Q&A turns from URL params
    this.route.queryParamMap
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((params) => {
        const query = params.get('q');
        this.searchQuery.set(query);

        // Read Q&A highlighted turns
        const turnsParam = params.get('turns');
        if (turnsParam) {
          const turnIndices = turnsParam.split(',')
            .map(t => parseInt(t.trim(), 10))
            .filter(t => !isNaN(t));
          this.highlightedTurns.set(new Set(turnIndices));
        } else {
          this.highlightedTurns.set(new Set());
        }
      });

    this.route.paramMap
      .pipe(
        map((params) => params.get('id')),
        filter((id): id is string => Boolean(id)),
        tap(() => {
          this.loading.set(true);
          this.error.set(null);
          this.transcript.set(null);
        }),
        switchMap((id) =>
          this.api.getTranscript(id).pipe(
            catchError(() => {
              this.error.set('Unable to load transcript details.');
              return of(null);
            }),
            finalize(() => this.loading.set(false))
          )
        ),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe((transcript) => {
        if (transcript) {
          this.transcript.set(transcript);
        }
      });
  }

  highlightOrganization(org: string): void {
    this.selectedOrg.set(org);
  }

  clearHighlight(): void {
    this.selectedOrg.set(null);
  }

  highlightOrgText(text: string): string {
    if (!text) return text;

    const org = this.selectedOrg();
    if (!org) return text;

    // Extract base name by removing common suffixes
    let searchTerms = [org];
    const baseName = org
      .replace(/\s+(Inc\.?|LLC|Corp\.?|Corporation|Ltd\.?|Limited|Co\.?)$/i, '')
      .trim();

    if (baseName !== org && baseName.length > 2) {
      searchTerms.push(baseName);
    }

    // Try each search term
    for (const term of searchTerms) {
      const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      const regex = new RegExp(`\\b(${escaped})\\b`, 'gi');
      const matches = text.match(regex);

      if (matches && matches.length > 0) {
        return text.replace(regex, (match) => {
          return `<mark class="org-highlight">${match}</mark>`;
        });
      }
    }

    return text;
  }

  highlightTurnText(turnText: string): string {
    let result = turnText;

    // First, highlight search query terms (if coming from search)
    const query = this.searchQuery();
    if (query) {
      // Check if query has quotes (exact phrase search)
      const quoteMatch = query.match(/"([^"]+)"/);
      if (quoteMatch) {
        // Exact phrase search
        const phrase = quoteMatch[1];
        const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const regex = new RegExp(`(${escaped})`, 'gi');
        result = result.replace(regex, '<mark class="search-highlight">$1</mark>');
      } else {
        // General search - highlight individual terms (excluding operators)
        const terms = query
          .split(/\s+/)
          .filter(term => term && !['AND', 'OR'].includes(term.toUpperCase()) && !term.startsWith('-'));

        terms.forEach(term => {
          const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          const regex = new RegExp(`\\b(${escaped})\\b`, 'gi');
          result = result.replace(regex, '<mark class="search-highlight">$1</mark>');
        });
      }
    }

    // Then, highlight selected organization (different color)
    const org = this.selectedOrg();
    if (org) {
      // Extract base name by removing common suffixes
      // e.g., "Apple Inc" -> "Apple", "Goldman Sachs" -> "Goldman Sachs"
      let searchTerms = [org]; // Start with full name

      // Add base name without common suffixes
      const baseName = org
        .replace(/\s+(Inc\.?|LLC|Corp\.?|Corporation|Ltd\.?|Limited|Co\.?)$/i, '')
        .trim();

      if (baseName !== org && baseName.length > 2) {
        searchTerms.push(baseName);
      }

      // Try each search term
      for (const term of searchTerms) {
        const escaped = term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        // Simple word boundary regex for Safari compatibility
        const regex = new RegExp(`\\b(${escaped})\\b`, 'gi');
        const matches = result.match(regex);

        if (matches && matches.length > 0) {
          // Found matches, apply highlighting
          result = result.replace(regex, (match) => {
            // Don't highlight if already inside a mark tag
            return `<mark class="org-highlight">${match}</mark>`;
          });
          break; // Stop after first successful match
        }
      }
    }

    return result;
  }

  formatTime(seconds: number | null): string {
    if (seconds === null) return '';
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  }
}
