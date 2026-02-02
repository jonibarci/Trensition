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
import { ApiService, QaResponse, SourceDetail, ConversationTurn } from '../../services/api.service';

interface Message {
  type: 'question' | 'answer';
  content: string;
  sources?: string[];
  detailed_sources?: SourceDetail[];
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
}
