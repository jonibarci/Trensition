import { RenderMode, ServerRoute } from '@angular/ssr';

export const serverRoutes: ServerRoute[] = [
  {
    path: '',
    renderMode: RenderMode.Prerender
  },
  {
    path: 'transcripts',
    renderMode: RenderMode.Prerender
  },
  {
    path: 'transcripts/:id',
    renderMode: RenderMode.Server
  },
  {
    path: 'qa',
    renderMode: RenderMode.Prerender
  },
  {
    path: 'ingest',
    renderMode: RenderMode.Prerender
  },
  {
    path: '**',
    renderMode: RenderMode.Server
  }
];
