# Production Deployment Guide

## 🚀 Hosting Options

### 1. **Cloud Platforms (Recommended)**

#### **Railway** (Easiest)
- ✅ Zero-config deployment
- ✅ Automatic HTTPS
- ✅ Git-based deployments
- 💰 $5/month for basic plans

**Deploy Steps:**
1. Push code to GitHub
2. Connect Railway to your repo
3. Add environment variables
4. Deploy automatically

#### **Render** (Great for small apps)
- ✅ Free tier available
- ✅ Automatic deployments
- ✅ Managed databases
- 💰 Free tier + $7/month for production

#### **DigitalOcean App Platform**
- ✅ Managed infrastructure
- ✅ Auto-scaling
- ✅ Built-in monitoring
- 💰 $5/month starter

#### **AWS Elastic Beanstalk**
- ✅ Enterprise-grade
- ✅ Auto-scaling
- ✅ Load balancing
- 💰 Pay for AWS resources used

#### **Google Cloud Run**
- ✅ Serverless
- ✅ Pay per request
- ✅ Fast cold starts
- 💰 Free tier + usage-based

### 2. **VPS/Dedicated Servers**

#### **DigitalOcean Droplet**
- 💰 $4-6/month
- 🛠️ Full control
- 📊 Excellent documentation

#### **Linode**
- 💰 $5/month
- 🛠️ Developer-friendly
- 📊 Great performance

#### **AWS EC2**
- 💰 Variable pricing
- 🛠️ Maximum flexibility
- 📊 Enterprise features

### 3. **Container Platforms**

#### **AWS ECS/Fargate**
- ✅ Managed containers
- ✅ Auto-scaling
- 💰 Pay per use

#### **Google Cloud Run**
- ✅ Serverless containers
- ✅ Fast deployment
- 💰 Usage-based pricing

## 🔧 Production Setup

### 1. **Environment Configuration**

Create `.env.production`:
```bash
# Required
GOOGLE_API_KEY=your_google_api_key
OPENAI_API_KEY=your_openai_api_key  # Fallback
ANTHROPIC_API_KEY=your_anthropic_api_key  # Fallback

# Production settings
ENVIRONMENT=production
HOST=0.0.0.0
PORT=8000
WORKERS=4
LOG_LEVEL=info

# Security
CORS_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# Performance
MAX_FILE_SIZE=10485760
MAX_BULK_FILES=20
REQUEST_TIMEOUT=60
```

### 2. **Docker Deployment**

```bash
# Build and run
docker build -t invoice-extractor .
docker run -p 8000:8000 --env-file .env.production invoice-extractor

# Or use docker-compose
docker-compose up -d
```

### 3. **Manual Deployment (VPS)**

```bash
# 1. Install dependencies
apt update && apt install python3.11 python3-pip nginx certbot

# 2. Clone repository
git clone your-repo-url
cd invoice-extractor

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.production .env
# Edit .env with your actual values

# 5. Run with gunicorn
gunicorn -c gunicorn.conf.py main:app

# 6. Setup nginx reverse proxy (see nginx.conf)
# 7. Setup SSL with Let's Encrypt
certbot --nginx -d yourdomain.com
```

## 🔒 Security Best Practices

### 1. **API Keys Management**
- Use environment variables (never commit to git)
- Rotate keys regularly
- Use different keys for different environments
- Monitor API usage

### 2. **CORS Configuration**
```python
# Only allow your actual domains
CORS_ORIGINS=https://yourdomain.com,https://app.yourdomain.com
```

### 3. **Rate Limiting**
```python
# Add to main.py
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter

@app.post("/extract-details/")
@limiter.limit("10/minute")  # 10 requests per minute
async def extract_invoice_details(request: Request, file: UploadFile = File(...)):
    # ... existing code
```

### 4. **Input Validation**
- File size limits (already implemented)
- File type validation (already implemented)
- Content scanning for malware

## 📊 Monitoring & Observability

### 1. **Health Checks**
```bash
# Check API health
curl https://yourdomain.com/health

# Check provider status
curl https://yourdomain.com/providers
```

### 2. **Logging**
```python
# Logs are configured in the application
# Monitor logs for:
# - API quota usage
# - Error rates
# - Processing times
# - Provider failures
```

### 3. **Metrics** (Optional)
- Add Prometheus metrics
- Monitor with Grafana
- Set up alerts for quota limits

## 🚀 Recommended Deployment Strategy

### For Small/Medium Scale:
1. **Railway** or **Render** for easiest deployment
2. Set up fallback providers (OpenAI + Anthropic)
3. Monitor API quotas
4. Use CDN for frontend assets

### For Enterprise Scale:
1. **AWS ECS** or **Google Cloud Run**
2. Multiple AI provider accounts
3. Load balancing
4. Database for caching results
5. Redis for rate limiting
6. Comprehensive monitoring

## 💡 Cost Optimization

### AI Provider Costs:
- **Google Gemini**: $0.075-0.15 per 1K characters
- **OpenAI GPT-4**: $10-30 per 1M tokens
- **Anthropic Claude**: $8-24 per 1M tokens

### Optimization Strategies:
1. Use cheaper models first (Gemini Flash)
2. Implement caching for duplicate files
3. Batch processing for bulk operations
4. Monitor and alert on quota usage

## 🔄 Auto-Fallback System

The production code includes:
- **3+ AI providers** with automatic failover
- **Quota monitoring** and provider rotation
- **Retry logic** with exponential backoff
- **Health checks** for provider availability
- **Graceful degradation** when providers fail

## 📋 Deployment Checklist

- [ ] Set up all environment variables
- [ ] Configure multiple AI providers
- [ ] Test fallback system
- [ ] Set up domain and SSL
- [ ] Configure monitoring
- [ ] Test file upload limits
- [ ] Verify CORS settings
- [ ] Set up backup/restore
- [ ] Monitor API quotas
- [ ] Set up alerting

## 🆘 Troubleshooting

### Common Issues:
1. **All providers failing**: Check API keys and quotas
2. **Slow responses**: Increase workers, check provider latency
3. **File upload errors**: Check file size limits and CORS
4. **Memory issues**: Reduce worker count or increase server RAM

### Debug Commands:
```bash
# Check provider status
curl https://yourdomain.com/providers

# Reset provider retries
curl -X POST https://yourdomain.com/reset-providers

# Check health
curl https://yourdomain.com/health
```
