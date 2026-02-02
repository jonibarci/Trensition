# TrendTracker Frontend

Implementation details for the TrendTracker Angular frontend. This document explains **how** the frontend application works, including SSR setup, routing strategy, API integration, and component architecture.

For high-level architecture and design decisions, see the [main README](../../README.md).

## Technology Stack

- **Angular 21** - Latest version with standalone components
- **TypeScript** - Type-safe development
- **RxJS** - Reactive state management and HTTP handling
- **Angular SSR** - Server-side rendering for SEO and performance
- **Node 20 LTS** - Runtime for SSR server

## Architecture

### SSR Strategy

TrendTracker uses **hybrid rendering** - different routes use different rendering modes:

**File**: `src/app/app.routes.server.ts`

```typescript
export const serverRoutes: ServerRoute[] = [
  { path: '', renderMode: RenderMode.Prerender },            // Static homepage
  { path: 'transcripts', renderMode: RenderMode.Prerender }, // Static list page
  { path: 'transcripts/:id', renderMode: RenderMode.Server },// Dynamic (depends on DB)
  { path: 'qa', renderMode: RenderMode.Prerender },          // Static Q&A form
  { path: 'ingest', renderMode: RenderMode.Prerender },      // Static ingestion form
  { path: '**', renderMode: RenderMode.Server }              // Fallback
];
```

**Why hybrid?**
- **Prerender** static pages at build time for instant loading
- **Server** render dynamic pages with DB-dependent content (transcript details)
- Balances performance (prerendered) with flexibility (server-rendered)

### API Integration

**Base URL Configuration** (in `src/environments/`):

```typescript
// environment.ts (development)
export const environment = {
  production: false,
  apiUrl: 'http://127.0.0.1:8000'
};

// environment.prod.ts (production)
export const environment = {
  production: true,
  apiUrl: 'http://localhost:8000'  // Adjust for Docker deployment
};
```

**Services** (in `src/app/services/`):
- `api.service.ts` - Centralized API client with typed interfaces
  - Uses `environment.apiUrl` for all requests
  - Provides methods for all backend endpoints

**HTTP Interceptors** (if any):
- Error handling (catch 4xx/5xx responses)
- Loading state management

### Component Structure

```
src/app/
├── pages/
│   ├── transcripts/           # List view with search
│   ├── transcript-detail/     # Full transcript with turns
│   ├── qa/                    # Question input and answer display
│   └── ingest/                # Company ticker input for ingestion
│
├── services/
│   └── api.service.ts         # Centralized API client
│                              # - Typed interfaces for all responses
│                              # - Methods for all backend endpoints
│
├── environments/
│   ├── environment.ts         # Development config
│   └── environment.prod.ts    # Production config
│
├── app.routes.ts              # Client-side routing
├── app.routes.server.ts       # SSR rendering modes
└── app.config.ts              # Providers and DI configuration
```

### Routing

**Client Routes** (`app.routes.ts`) use **lazy loading** for better performance:

```typescript
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'transcripts' },
  {
    path: 'transcripts',
    loadComponent: () => import('./pages/transcripts/transcripts').then(m => m.Transcripts)
  },
  {
    path: 'transcripts/:id',
    loadComponent: () => import('./pages/transcript-detail/transcript-detail')
      .then(m => m.TranscriptDetail)
  },
  { path: 'qa', loadComponent: () => import('./pages/qa/qa').then(m => m.Qa) },
  { path: 'ingest', loadComponent: () => import('./pages/ingest/ingest').then(m => m.Ingest) }
];
```

**Benefits of lazy loading**:
- Components only loaded when route is visited
- Smaller initial bundle size
- Faster initial load time

**Dynamic Parameters**:
- `transcripts/:id` - Transcript ID format: `yahoo_AAPL_369370`
- Accessed via: `inject(ActivatedRoute).params` or router signals

### State Management

Uses **RxJS Observables** for reactive HTTP requests:

**Example**: API calls using the centralized service

```typescript
import { ApiService } from '../services/api.service';

export class TranscriptsComponent {
  private readonly apiService = inject(ApiService);

  loadTranscripts() {
    this.apiService.listTranscripts({ company: 'AAPL' })
      .subscribe(transcripts => {
        this.transcripts = transcripts;
      });
  }
}
```

**Benefits**:
- Type-safe interfaces for all API responses
- Centralized error handling
- Observable-based for easy composition

## Local Development

### Prerequisites

- Node.js 20 LTS
- npm 10+

### Installation

```bash
cd frontend/ui

# Install dependencies
npm install
```

### Development Server

```bash
# Start with SSR (recommended)
npm run serve:ssr:ui

# Or use ng serve (no SSR, faster reload)
ng serve
```

- **With SSR**: http://localhost:4000 (matches production)
- **Without SSR**: http://localhost:4200 (dev mode only)

### Building

```bash
# Build for production (with SSR)
npm run build

# Build for development
npm run build -- --configuration development
```

**Output**: `dist/ui/` contains both browser and server bundles

### SSR Server

Production SSR server:

```bash
# Build first
npm run build

# Start SSR server
node dist/ui/server/server.mjs
```

Server listens on port 4000 by default.

## Docker Deployment

**Multi-stage Dockerfile**:

```dockerfile
# Stage 1: Build
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# Stage 2: Production
FROM node:20-alpine
WORKDIR /app
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/package*.json ./
RUN npm ci --omit=dev
EXPOSE 4000
CMD ["node", "dist/ui/server/server.mjs"]
```

**Why multi-stage?**
- Smaller final image (no build tools in production)
- Faster startup (only production dependencies)
- Better security (minimal attack surface)

## Key Implementation Details

### Transcript Detail Page

**Features**:
- Full transcript text with speaker names
- Turn-by-turn display
- Highlighted turns (when accessed from Q&A sources)
- Metadata: company, quarter, year, event ID

**Highlighting Implementation**:
- URL param: `?highlight=5,12,18` (turn indices from Q&A)
- Parse indices: `route.queryParams.subscribe(params => ...)`
- Apply CSS class to matching turn elements

### Q&A Interface

**Flow**:
1. User types question
2. Optionally filters by company/transcript
3. Submit → API call to `/qa`
4. Display answer with cited sources
5. Sources link to transcript detail page with highlighted turns

**Answer Display**:
- Markdown rendering for formatted text
- Source citations as clickable chips
- Loading spinner during API call
- Error handling for API failures

### Cookie Manager

**Features**:
- Check cookie status (valid/invalid)
- Refresh cookies (opens browser if not headless)
- Delete cookies (for testing)

**Status Indicators**:
- Green checkmark: Cookies valid
- Red X: Cookies expired/invalid
- Loading spinner: Checking status

**Implementation**:
```typescript
checkStatus() {
  this.cookieService.getStatus().subscribe({
    next: (response) => this.cookieStatus = response.valid ? 'valid' : 'invalid',
    error: (err) => this.cookieStatus = 'error'
  });
}
```

## Troubleshooting

### CORS Errors

```
Access to XMLHttpRequest blocked by CORS policy
```

- **Cause**: Backend not configured to allow frontend origin
- **Fix**: Update CORS middleware in `backend/app/main.py`:
  ```python
  allow_origins=["http://localhost:4000", "http://localhost:4200"]
  ```

### SSR Build Failures

```
Error: Cannot prerender routes with dynamic parameters
```

- **Cause**: Route uses `Prerender` mode but has `:id` params
- **Fix**: Change to `Server` mode in `app.routes.server.ts`:
  ```typescript
  { path: 'transcripts/:id', renderMode: RenderMode.Server }
  ```

### API Connection Issues

**Development** (ng serve on 4200):
- Backend API must be running on port 8000
- Check `environment.ts` has correct API URL

**Production** (Docker):
- Backend container must be named `backend` in docker-compose
- API URL should use internal network: `http://backend:8000`

### Hydration Errors

```
NG0500: During hydration Angular expected...
```

- **Cause**: Client-side state differs from server-side rendered HTML
- **Common causes**:
  - Random data generation in templates
  - Date/time formatting differences
  - Async data not awaited in SSR
- **Fix**: Use `isPlatformBrowser()` to skip problematic code on server

## Development Tips

### Code Generation

```bash
# Generate new component
ng generate component components/my-component

# Generate service
ng generate service services/my-service

# Generate interface
ng generate interface models/my-model
```

### Type Safety

Always define TypeScript interfaces for API responses:

```typescript
// models/transcript.model.ts
export interface Transcript {
  id: string;
  company_name: string;
  fiscal_quarter: number;
  fiscal_year: number;
  full_text: string;
  turns: TranscriptTurn[];
}
```

Use in services:
```typescript
getTranscript(id: string): Observable<Transcript> {
  return this.http.get<Transcript>(`${API_URL}/transcripts/${id}`);
}
```

### Testing

```bash
# Run unit tests
npm run test

# Run e2e tests (if configured)
npm run e2e
```

## Additional Resources

- [Angular Documentation](https://angular.dev)
- [Angular SSR Guide](https://angular.dev/guide/ssr)
- [RxJS Documentation](https://rxjs.dev)
- Backend API docs: http://localhost:8000/docs
