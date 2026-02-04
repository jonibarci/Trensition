import { CommonModule } from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  inject,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { finalize } from 'rxjs';

import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ApiService, QaResponse, SourceDetail, ConversationTurn, TranscriptDetail, Turn } from '../../services/api.service';

interface Message {
  type: 'question' | 'answer';
  content: string;
  sources?: string[];
  detailed_sources?: SourceDetail[];
}

interface ReferencedSection {
  transcriptId: string;
  title: string;
  date: string;
  turnIndices: number[];
  turns: Turn[];
}

@Component({
  selector: 'app-qa',
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './qa.html',
  styleUrl: './qa.css',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class Qa {
  private readonly api = inject(ApiService);
  private readonly destroyRef = inject(DestroyRef);

  protected readonly company = signal('');
  protected readonly question = signal('');
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly messages = signal<Message[]>([]);
  protected readonly conversationHistory = signal<ConversationTurn[]>([]);
  protected readonly transcriptCache = signal<Map<string, TranscriptDetail>>(new Map());
  protected readonly transcriptLoading = signal<Set<string>>(new Set());
  protected readonly openReferences = signal<Set<string>>(new Set());

  get companyValue(): string {
    return this.company();
  }

  set companyValue(value: string) {
    this.company.set(value);
  }

  get questionValue(): string {
    return this.question();
  }

  set questionValue(value: string) {
    this.question.set(value);
  }

  ask(): void {
    const question = this.question().trim();
    if (!question) {
      this.error.set('Question is required.');
      return;
    }

    // Add question to messages
    this.messages.update(msgs => [...msgs, {
      type: 'question',
      content: question
    }]);

    this.loading.set(true);
    this.error.set(null);

    // Clear the question input
    const currentQuestion = question;
    this.question.set('');

    this.api
      .askQuestion({
        question: currentQuestion,
        company: this.company().trim() || undefined,
        conversation_history: this.conversationHistory()
      })
      .pipe(
        finalize(() => this.loading.set(false)),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (response) => {
          // Add answer to messages
          this.messages.update(msgs => [...msgs, {
            type: 'answer',
            content: response.answer,
            sources: response.sources,
            detailed_sources: response.detailed_sources
          }]);

          // Preload transcript references for the answer sidebar
          (response.detailed_sources || []).forEach((source) => {
            this.ensureTranscriptLoaded(source.transcript_id);
          });

          // Update conversation history
          this.conversationHistory.update(history => [...history, {
            question: currentQuestion,
            answer: response.answer
          }]);
        },
        error: () => {
          this.error.set('Unable to fetch an answer right now.');
          // Remove the question from messages on error
          this.messages.update(msgs => msgs.slice(0, -1));
        },
      });
  }

  clearConversation(): void {
    this.messages.set([]);
    this.conversationHistory.set([]);
    this.error.set(null);
  }

  getTurnIndicesForMessage(message: Message): number[] {
    if (!message.detailed_sources || message.detailed_sources.length === 0) {
      return [];
    }
    // Get turn indices from the first source (for simplicity)
    return message.detailed_sources[0]?.turn_indices || [];
  }

  getSourcesForMessage(message: Message): string[] {
    return message.sources || [];
  }

  getTurnIndicesForSource(transcriptId: string, message: Message): number[] {
    const detailedSources = message.detailed_sources || [];
    const sourceDetail = detailedSources.find(s => s.transcript_id === transcriptId);
    return sourceDetail?.turn_indices || [];
  }

  toggleReference(messageIndex: number, transcriptId: string): void {
    const key = `${messageIndex}:${transcriptId}`;
    const isOpen = this.openReferences().has(key);
    this.openReferences.update((set) => {
      const next = new Set(set);
      if (isOpen) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });

    if (!isOpen) {
      this.ensureTranscriptLoaded(transcriptId);
    }
  }

  isReferenceOpen(messageIndex: number, transcriptId: string): boolean {
    return this.openReferences().has(`${messageIndex}:${transcriptId}`);
  }

  hasOpenReferences(messageIndex: number, message: Message): boolean {
    if (!message.detailed_sources || message.detailed_sources.length === 0) {
      return false;
    }
    return message.detailed_sources.some(source => this.isReferenceOpen(messageIndex, source.transcript_id));
  }

  getReferencedSections(message: Message, messageIndex?: number): ReferencedSection[] {
    const detailedSources = message.detailed_sources || [];
    const cache = this.transcriptCache();
    return detailedSources
      .filter((source) => {
        if (messageIndex === undefined) {
          return true;
        }
        return this.isReferenceOpen(messageIndex, source.transcript_id);
      })
      .map((source) => {
      const transcript = cache.get(source.transcript_id);
      const turnIndices = source.turn_indices || [];
      const turns = transcript
        ? turnIndices
            .map((index) => transcript.turns.find(turn => turn.index === index))
            .filter((turn): turn is Turn => Boolean(turn))
        : [];
      return {
        transcriptId: source.transcript_id,
        title: transcript?.title || source.transcript_id,
        date: transcript?.date || '',
        turnIndices,
        turns,
      };
    });
  }

  formatTurnLabel(turn: Turn): string {
    if (!turn) return '';
    return turn.speaker_name ? `Turn ${turn.index} · ${turn.speaker_name}` : `Turn ${turn.index}`;
  }

  isTranscriptLoading(transcriptId: string): boolean {
    return this.transcriptLoading().has(transcriptId);
  }

  private ensureTranscriptLoaded(transcriptId: string): void {
    if (this.transcriptCache().has(transcriptId) || this.transcriptLoading().has(transcriptId)) {
      return;
    }

    this.transcriptLoading.update((set) => {
      const next = new Set(set);
      next.add(transcriptId);
      return next;
    });

    this.api
      .getTranscript(transcriptId)
      .pipe(
        finalize(() => {
          this.transcriptLoading.update((set) => {
            const next = new Set(set);
            next.delete(transcriptId);
            return next;
          });
        }),
        takeUntilDestroyed(this.destroyRef)
      )
      .subscribe({
        next: (transcript) => {
          this.transcriptCache.update((cache) => {
            const next = new Map(cache);
            next.set(transcriptId, transcript);
            return next;
          });
        },
        error: () => {
          // Ignore errors; sidebar will show empty state.
        },
      });
  }
}
