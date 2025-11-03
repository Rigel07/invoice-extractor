# 🧾 Invoice Extractor Pro

AI-powered invoice processing application that extracts data from invoice images and generates Tally-compatible XML files.

## Features

- **AI-Powered Extraction**: Uses Google Gemini AI to extract structured data from invoice images
- **Flexible GST Support**: Automatically detects and handles different GST rates (5%, 12%, 18%, 28%)
- **Transaction Types**: Supports both Sales and Purchase invoice processing
- **Tally Integration**: Generates clean XML files that import seamlessly into TallyPrime
- **Batch Processing**: Handle multiple invoices simultaneously with real-time progress tracking
- **Smart Caching**: Redis-based caching with bypass option to reduce AI API costs

## Quick Start

### 1. Install Dependencies

**Frontend:**
```bash
npm install
```

**Backend:**
```bash
pip install -r requirements.txt
```

### 2. Environment Setup

Copy `.env.example` to `.env` and fill in your configuration:
```bash
cp .env.example .env
```

Required variables:
- `GOOGLE_API_KEY`: Your Google Gemini AI API key
- `REDIS_URL`: Redis connection string

### 3. Run the Application

**Start Backend:**
```bash
python src/main.py
```

**Start Frontend:**
```bash
npm run dev
```

Visit `http://localhost:5173` to use the application.

## Tech Stack

- **Frontend**: React + Vite
- **Backend**: FastAPI + Python
- **AI**: Google Gemini
- **Cache**: Redis
- **Output**: Tally XML + CSV

## Documentation

See `DEVELOPMENT_NOTES.md` for comprehensive technical documentation and implementation details.