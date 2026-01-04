# Deployment Guide - AI Consultant Platform

## 🚀 Production Deployment Options

### Option 1: AWS Deployment (Recommended)

#### Requirements:
- AWS Account
- Domain name
- SSL certificate

#### Architecture:
```
┌─────────────────┐
│   CloudFront    │ (CDN + SSL)
└────────┬────────┘
         │
┌────────▼────────┐
│   ALB/ELB       │ (Load Balancer)
└────────┬────────┘
         │
┌────────▼────────┐
│   EC2/ECS       │ (Application Servers)
│   - FastAPI     │
│   - WebSocket   │
└────────┬────────┘
         │
┌────────▼────────┐
│   ElastiCache   │ (Redis for sessions)
└─────────────────┘
```

#### Step-by-Step:

1. **Setup EC2 Instance**
   ```bash
   # Launch Ubuntu 22.04 instance (t3.medium or larger)
   # Security group: Allow 80, 443, 8000

   # SSH into instance
   ssh -i your-key.pem ubuntu@your-instance-ip

   # Install dependencies
   sudo apt update
   sudo apt install python3.10 python3-pip nginx certbot
   ```

2. **Deploy Application**
   ```bash
   # Clone your repository
   git clone https://github.com/youruser/ai-consultant-platform.git
   cd ai-consultant-platform

   # Install Python dependencies
   pip3 install -r requirements.txt

   # Configure environment
   cp .env.example .env
   nano .env  # Add your API keys
   ```

3. **Setup Systemd Service**
   ```bash
   # Create service file
   sudo nano /etc/systemd/system/ai-consultant.service
   ```

   ```ini
   [Unit]
   Description=AI Consultant Platform
   After=network.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/home/ubuntu/ai-consultant-platform
   Environment="PATH=/home/ubuntu/.local/bin"
   ExecStart=/usr/bin/python3 backend/main.py
   Restart=always

   [Install]
   WantedBy=multi-user.target
   ```

   ```bash
   # Enable and start service
   sudo systemctl enable ai-consultant
   sudo systemctl start ai-consultant
   sudo systemctl status ai-consultant
   ```

4. **Configure Nginx**
   ```bash
   sudo nano /etc/nginx/sites-available/ai-consultant
   ```

   ```nginx
   server {
       listen 80;
       server_name your-domain.com;

       location / {
           proxy_pass http://localhost:8000;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }

       location /ws/ {
           proxy_pass http://localhost:8000;
           proxy_http_version 1.1;
           proxy_set_header Upgrade $http_upgrade;
           proxy_set_header Connection "upgrade";
       }
   }
   ```

   ```bash
   # Enable site
   sudo ln -s /etc/nginx/sites-available/ai-consultant /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl restart nginx
   ```

5. **Setup SSL with Let's Encrypt**
   ```bash
   sudo certbot --nginx -d your-domain.com
   ```

---

### Option 2: Google Cloud Platform

```bash
# Deploy to Cloud Run (serverless)
gcloud run deploy ai-consultant \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated
```

---

### Option 3: DigitalOcean App Platform

1. Connect your GitHub repository
2. Configure environment variables
3. Deploy automatically on push

---

### Option 4: Docker Deployment

**Dockerfile:**
```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["python", "backend/main.py"]
```

**docker-compose.yml:**
```yaml
version: '3.8'

services:
  app:
    build: .
    ports:
      - "8000:8000"
    environment:
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - REDIS_URL=redis://redis:6379
    depends_on:
      - redis

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf
    depends_on:
      - app
```

Deploy with:
```bash
docker-compose up -d
```

---

## 📊 Monitoring & Analytics

### Application Monitoring

1. **Setup Sentry (Error Tracking)**
   ```python
   import sentry_sdk
   sentry_sdk.init(dsn="your-sentry-dsn")
   ```

2. **Prometheus Metrics**
   ```python
   from prometheus_fastapi_instrumentator import Instrumentator
   Instrumentator().instrument(app).expose(app)
   ```

3. **CloudWatch (AWS)**
   - Monitor EC2 metrics
   - Track API latency
   - Set up alarms

### Business Analytics

- Track session counts
- Monitor revenue metrics
- User engagement analytics
- Consultant mode popularity

---

## 🔒 Security Checklist

- [ ] Use HTTPS/SSL everywhere
- [ ] Set strong SECRET_KEY in production
- [ ] Enable rate limiting
- [ ] Implement authentication
- [ ] Secure API keys (use AWS Secrets Manager / GCP Secret Manager)
- [ ] Regular security updates
- [ ] CORS configuration
- [ ] Input validation
- [ ] SQL injection protection (if using SQL)
- [ ] XSS protection

---

## 💰 Cost Optimization

### Gemini API Costs
- Monitor usage per session
- Implement caching for similar frames
- Adjust FRAMES_PER_ANALYSIS to balance cost/quality

### Infrastructure Costs
- Start with smaller instances
- Use auto-scaling based on demand
- Implement CDN caching
- Optimize video quality

### Estimated Monthly Costs (1000 sessions/month):
- **EC2**: $50-100
- **Redis**: $20-40
- **CloudFront**: $10-30
- **Gemini API**: $200-500 (varies by usage)
- **Total**: ~$280-670/month

**Revenue (1000 sessions @ avg $150)**: $150,000/month
**Profit Margin**: ~99.5%

---

## 📈 Scaling Strategy

### Stage 1: MVP (0-100 users)
- Single EC2 instance
- Basic monitoring

### Stage 2: Growth (100-1000 users)
- Multiple instances + load balancer
- Redis for session management
- Enhanced monitoring

### Stage 3: Scale (1000+ users)
- Auto-scaling groups
- CDN for static assets
- Database for user management
- Kubernetes orchestration

---

## 🔄 CI/CD Pipeline

**GitHub Actions Example:**
```yaml
name: Deploy

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Deploy to production
        run: |
          ssh user@server 'cd /app && git pull && systemctl restart ai-consultant'
```

---

## 📞 Support & Maintenance

- Set up health check endpoint
- Automated backups
- Regular dependency updates
- Log rotation
- Database maintenance (if applicable)

---

## 🎯 Go-Live Checklist

- [ ] Domain configured
- [ ] SSL certificate installed
- [ ] Environment variables set
- [ ] Database migrations run (if applicable)
- [ ] Monitoring enabled
- [ ] Backup strategy in place
- [ ] Load testing completed
- [ ] Security audit passed
- [ ] Payment processing tested
- [ ] Terms of service & privacy policy published
