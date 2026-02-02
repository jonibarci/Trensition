import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

export interface Company {
  company: string;
  ticker: string;
}

export interface TranscriptListItem {
  id: string;
  company: string;
  ticker?: string;
  title: string;
  date: string;
  snippet: string;
}

export interface Speaker {
  id: number;
  name: string;
  role: string | null;
  org: string | null;
}

export interface Turn {
  index: number;
  speaker_id: number;
  speaker_name: string;
  text: string;
  start: number | null;
  end: number | null;
}

export interface TranscriptDetail {
  id: string;
  company: string;
  ticker?: string;
  title: string;
  date: string;
  quarter: string;
  fiscal_year: string;
  organizations: string[];
  speakers: Speaker[];
  turns: Turn[];
  text: string;
  raw_transcriptcontent: any;
}

export interface IngestResponse {
  ok: boolean;
  company: string;
  ingested: number;
  message: string;
}

export interface CookieStatus {
  valid: boolean;
  message: string;
  cookie_file_exists: boolean;
}

export interface CookieRefreshResponse {
  ok: boolean;
  message: string;
}

export interface SourceDetail {
  transcript_id: string;
  turn_indices: number[];
}

export interface ConversationTurn {
  question: string;
  answer: string;
}

export interface QaResponse {
  answer: string;
  sources: string[];
  detailed_sources?: SourceDetail[];
}

@Injectable({
  providedIn: 'root',
})
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly baseUrl = environment.apiUrl;

  getCompanies(): Observable<Company[]> {
    return this.http.get<Company[]>(`${this.baseUrl}/companies`);
  }

  listTranscripts(params: {
    company?: string;
    q?: string;
    limit?: number;
    offset?: number;
  }): Observable<TranscriptListItem[]> {
    let httpParams = new HttpParams();

    if (params.company) {
      httpParams = httpParams.set('company', params.company);
    }
    if (params.q) {
      httpParams = httpParams.set('q', params.q);
    }
    if (typeof params.limit === 'number') {
      httpParams = httpParams.set('limit', params.limit.toString());
    }
    if (typeof params.offset === 'number') {
      httpParams = httpParams.set('offset', params.offset.toString());
    }

    return this.http.get<TranscriptListItem[]>(
      `${this.baseUrl}/transcripts`,
      { params: httpParams }
    );
  }

  getTranscript(id: string): Observable<TranscriptDetail> {
    return this.http.get<TranscriptDetail>(`${this.baseUrl}/transcripts/${id}`);
  }

  ingestCompany(company: string, limit?: number): Observable<IngestResponse> {
    const payload: any = { company };
    if (limit !== undefined && limit !== null) {
      payload.limit = limit;
    }
    return this.http.post<IngestResponse>(`${this.baseUrl}/ingest`, payload);
  }

  getCookieStatus(): Observable<CookieStatus> {
    return this.http.get<CookieStatus>(`${this.baseUrl}/yahoo/cookies/status`);
  }

  refreshCookies(): Observable<CookieRefreshResponse> {
    return this.http.post<CookieRefreshResponse>(`${this.baseUrl}/yahoo/cookies/refresh`, {});
  }

  deleteCookies(): Observable<CookieRefreshResponse> {
    return this.http.delete<CookieRefreshResponse>(`${this.baseUrl}/yahoo/cookies`);
  }

  askQuestion(payload: {
    question: string;
    company?: string;
    transcriptId?: string;
    conversation_history?: ConversationTurn[];
  }): Observable<QaResponse> {
    return this.http.post<QaResponse>(`${this.baseUrl}/qa`, payload);
  }
}
