import { Routes } from '@angular/router';

export const routes: Routes = [
  {
    path: '',
    pathMatch: 'full',
    redirectTo: 'transcripts',
  },
  {
    path: 'transcripts',
    loadComponent: () =>
      import('./pages/transcripts/transcripts').then((m) => m.Transcripts),
  },
  {
    path: 'transcripts/:id',
    loadComponent: () =>
      import('./pages/transcript-detail/transcript-detail').then(
        (m) => m.TranscriptDetail
      ),
  },
  {
    path: 'ingest',
    loadComponent: () =>
      import('./pages/ingest/ingest').then((m) => m.Ingest),
  },
  {
    path: 'qa',
    loadComponent: () => import('./pages/qa/qa').then((m) => m.Qa),
  },
];
