# Invoice Extractor

Production-ready invoice data extraction service built with React frontend and FastAPI backend. Extract structured data from invoice images and PDFs using Google's Gemini AI.

## Features

- **Multi-format Support**: Process both image files (JPG, PNG) and PDF documents
- **Batch Processing**: Handle up to 10 invoices simultaneously
- **Free Tier Optimized**: Process 500+ invoices daily within Google AI's free tier
- **Production Ready**: Docker containerization with Nginx and Gunicorn

## Quick Start

### Prerequisites
- Node.js 18+ and npm
- Python 3.8+
- Google AI API key

### Installation

1. **Setup**:
   ```bash
   git clone <repository-url>
   cd invoice-extractor
   npm install
   pip install -r requirements.txt
   ```

2. **Configure**:
   ```bash
   cp .env.example .env
   # Add your GOOGLE_AI_API_KEY to .env
   ```

3. **Run**:
   ```bash
   # Frontend
   npm run dev
   
   # Backend
   python src/backend/start.py
   ```

## API Endpoints

### Extract Invoice Data
```
POST /extract
Content-Type: multipart/form-data
Body: files (up to 10 invoices)
```

**Response**:
```json
{
  "results": [
    {
      "filename": "invoice.pdf",
      "data": {
        "vendor_name": "Company ABC",
        "invoice_number": "INV-001",
        "invoice_date": "2024-01-15",
        "total_amount": "1250.00",
        "currency": "USD",
        "line_items": [...]
      }
    }
  ]
}
```

## Production Deployment

```bash
# Docker deployment
docker-compose up --build -d

# Manual deployment
pip install -r requirements.txt
npm run build
gunicorn -c gunicorn.conf.py src.backend.main:app
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_AI_API_KEY` | Yes | Google AI API key |
| `ENVIRONMENT` | No | Set to 'production' for production mode |

## Tech Stack

- React + Vite frontend
- FastAPI backend
- Google Gemini 1.5 Pro
- Docker + Nginx + Gunicorn
