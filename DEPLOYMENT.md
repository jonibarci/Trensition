# Trensition Deployment Guide

This guide covers deploying Trensition using Docker.

## Prerequisites

- Docker Engine 20.10+
- Docker Compose 2.0+
- OpenAI API key (for Q&A and organization extraction features)

## Quick Start

1. **Clone the repository** (or navigate to the project directory)

```bash
cd Trensition
```

2. **Create environment file**

```bash
cp .env.example .env
```

Edit `.env` and set your OpenAI API key:

```bash
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/earnings
OPENAI_API_KEY=sk-your-actual-openai-api-key
```

3. **Build and start all services**

```bash
docker compose up -d
```

This will start:
- PostgreSQL database (port 5432)
- Backend API (port 8000)
- Frontend UI (port 4000)

4. **Initialize the database schema**

```bash
docker compose exec backend uv run python -m app.database.db --init
```

5. **Access the application**

- Frontend: http://localhost:4000
- Backend API: http://localhost:8000
- API Docs: http://localhost:8000/docs

## Yahoo Finance Setup

Before ingesting transcripts, you need Yahoo Finance consent cookies:

```bash
# Refresh cookies via API (runs headless in Docker)
curl -X POST http://localhost:8000/yahoo/cookies/refresh \
  -H "Content-Type: application/json" -d '{}'

# Or use the frontend UI at http://localhost:4000
```

This will refresh cookies in headless mode (takes ~8 seconds). The cookies will be saved automatically.

## Common Operations

### View logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f backend
docker compose logs -f frontend
docker compose logs -f postgres
```

### Restart services

```bash
# All services
docker compose restart

# Specific service
docker compose restart backend
```

### Stop services

```bash
docker compose down
```

### Stop and remove volumes (clean slate)

```bash
docker compose down -v
```

### Rebuild after code changes

```bash
# Rebuild all services
docker compose up -d --build

# Rebuild specific service
docker compose up -d --build backend
```

## Database Management

### Connect to PostgreSQL

```bash
docker compose exec postgres psql -U postgres -d earnings
```

### Backup database

```bash
docker compose exec postgres pg_dump -U postgres earnings > backup.sql
```

### Restore database

```bash
docker compose exec -T postgres psql -U postgres earnings < backup.sql
```

### View ticker statistics

```bash
docker compose exec backend uv run python -m app.database.db --show AAPL
```

## API Usage

### Health check

```bash
curl http://localhost:8000/health
```

### Ingest transcripts

```bash
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"company": "AAPL", "limit": 5}'
```

### Search transcripts

```bash
curl "http://localhost:8000/transcripts?company=AAPL&q=revenue"
```

### Ask questions

```bash
curl -X POST http://localhost:8000/qa \
  -H "Content-Type: application/json" \
  -d '{
    "question": "What was the revenue growth?",
    "company": "AAPL"
  }'
```

## Production Deployment

### Environment Variables

For production, set these environment variables:

```bash
# .env
DATABASE_URL=postgresql://user:password@your-db-host:5432/earnings
OPENAI_API_KEY=sk-prod-key
NODE_ENV=production
```

### Using External PostgreSQL

If using a managed PostgreSQL service (AWS RDS, Google Cloud SQL, etc.):

1. Update `DATABASE_URL` in `.env` to point to your external database
2. Ensure pgvector extension is installed: `CREATE EXTENSION vector;`
3. Remove or comment out the `postgres` service in `docker compose.yml`
4. Update backend `depends_on` to remove postgres dependency

Example for external database:

```yaml
services:
  backend:
    # ... other config ...
    environment:
      DATABASE_URL: ${DATABASE_URL}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
    # Remove depends_on postgres
```

### SSL/TLS Termination

For production, use a reverse proxy (nginx, Caddy, Traefik) for:
- HTTPS/SSL termination
- Domain routing
- Rate limiting
- Caching static assets

Example nginx config:

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:4000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    location /api {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Scaling

For horizontal scaling:

1. Run multiple backend containers behind a load balancer
2. Use external PostgreSQL (not container)
3. Use environment variables or secrets manager to share cookies
4. The backend automatically creates `.yahoo_cookies/storage_state.json` on first refresh
4. Consider Redis for caching

## Troubleshooting

### Backend won't start

Check logs:
```bash
docker compose logs backend
```

Common issues:
- Missing `OPENAI_API_KEY` in `.env`
- Database not accessible (check `DATABASE_URL`)
- Port 8000 already in use

### Database connection errors

```bash
# Check postgres is running
docker compose ps postgres

# Check postgres logs
docker compose logs postgres

# Test connection
docker compose exec postgres pg_isready -U postgres
```

### Frontend build fails

```bash
# Check logs
docker compose logs frontend

# Rebuild with no cache
docker compose build --no-cache frontend
```

### Yahoo consent errors

```bash
# Refresh consent cookies via API
curl -X POST http://localhost:8000/yahoo/cookies/refresh \
  -H "Content-Type: application/json" -d '{}'

# Or check cookie status
curl http://localhost:8000/yahoo/cookies/status

# Verify cookies exist in container
docker compose exec backend ls -la .yahoo_cookies/
```

### Out of disk space

```bash
# Remove unused Docker resources
docker system prune -a

# Remove unused volumes
docker volume prune
```

## Development vs Production

### Development (Local)

Use the local setup without Docker for faster iteration:

```bash
# Backend
cd backend
uv run uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend/ui
npm start
```

### Production (Docker)

Use Docker Compose for consistent deployments:

```bash
docker compose up -d
```

## Monitoring

### Container health

```bash
docker compose ps
```

### Resource usage

```bash
docker stats
```

### Application health

```bash
# Backend health
curl http://localhost:8000/health

# Frontend health (check if page loads)
curl -I http://localhost:4000
```

## Security Considerations

1. **Change default passwords**: Update PostgreSQL password in production
2. **Secure API keys**: Use secrets management (AWS Secrets Manager, HashiCorp Vault)
3. **Network isolation**: Use Docker networks to isolate services
4. **Read-only volumes**: Mount sensitive files as read-only when possible
5. **Regular updates**: Keep base images updated (`docker compose pull`)

## Support

For issues or questions:
- Check logs: `docker compose logs`
- Review [Backend README](backend/README.md)
- Review [Frontend README](frontend/ui/README.md)
