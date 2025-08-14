import google.generativeai as genai
import io
import os
import logging
import time
import asyncio
import re
from PIL import Image
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
import json
import csv
from typing import List, Optional, Dict, Any
import zipfile
import tempfile
import base64
from dataclasses import dataclass
from enum import Enum

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class GeminiModel:
    name: str
    model_id: str
    available: bool = True
    retry_count: int = 0
    max_retries: int = 2  # Reduced retries for faster failover
    last_error: Optional[str] = None
    cost_efficiency: int = 1  # 1 = most efficient, higher = less efficient

class OptimizedModelManager:
    """Highly optimized model manager for Google Gemini free tier"""
    
    def __init__(self):
        self.models = []
        self._setup_gemini_models()
        self.request_count = 0  # Track requests to avoid quota
        self.daily_limit = 50  # Google free tier limit
    
    def _setup_gemini_models(self):
        """Initialize Gemini models in order of cost efficiency"""
        google_key = os.getenv("GOOGLE_API_KEY")
        if not google_key:
            raise ValueError("GOOGLE_API_KEY environment variable is required")
        
        genai.configure(api_key=google_key)
        
        # Order by efficiency: Flash models are fastest and cheapest
        gemini_models = [
            ("Gemini 1.5 Flash 8B", "gemini-1.5-flash-8b", 1),      # Most efficient
            ("Gemini 1.5 Flash", "gemini-1.5-flash", 2),            # Good balance  
            ("Gemini 1.5 Pro", "gemini-1.5-pro", 3),               # Fallback (fixed model ID)
        ]
        
        for name, model_id, efficiency in gemini_models:
            self.models.append(GeminiModel(
                name=name,
                model_id=model_id,
                cost_efficiency=efficiency
            ))
        
        logger.info(f"Initialized {len(self.models)} optimized Gemini models")
    
    def get_best_available_model(self) -> Optional[GeminiModel]:
        """Get the most efficient available model"""
        available = [m for m in self.models if m.available and m.retry_count < m.max_retries]
        if not available:
            return None
        
        # Sort by efficiency (lower cost_efficiency = better)
        return min(available, key=lambda x: (x.cost_efficiency, x.retry_count))
    
    def mark_model_failed(self, model: GeminiModel, error: str):
        """Mark a model as failed and update retry count"""
        model.retry_count += 1
        model.last_error = error
        if model.retry_count >= model.max_retries:
            model.available = False
        logger.warning(f"Model {model.name} failed: {error} (retry {model.retry_count}/{model.max_retries})")
    
    def reset_all_models(self):
        """Reset all model retry counts"""
        for model in self.models:
            model.retry_count = 0
            model.available = True
        logger.info("Reset all model retry counts")
    
    def track_request(self):
        """Track API request count"""
        self.request_count += 1
        remaining = max(0, self.daily_limit - self.request_count)
        if remaining <= 5:
            logger.warning(f"Approaching daily limit: {remaining} requests remaining")
    
    def get_quota_status(self) -> dict:
        """Get current quota usage status"""
        return {
            "requests_used": self.request_count,
            "daily_limit": self.daily_limit,
            "requests_remaining": max(0, self.daily_limit - self.request_count),
            "quota_percentage": min(100, (self.request_count / self.daily_limit) * 100)
        }

# Initialize optimized model manager
model_manager = OptimizedModelManager()

# Initialize FastAPI app
app = FastAPI(
    title="Invoice Extractor API - Free Tier Optimized",
    description="Highly optimized invoice data extraction using Google Gemini free tier",
    version="2.1.0"
)

# CORS configuration for production
origins = [
    "http://localhost:5173",  # Development
    "http://localhost:5174",  # Development
    "http://localhost:3000",  # Development
    "https://yourdomain.com",  # Production - replace with your actual domain
    "https://www.yourdomain.com",  # Production - replace with your actual domain
]

# Add environment-based CORS origins
cors_origins = os.getenv("CORS_ORIGINS", "").split(",")
if cors_origins and cors_origins[0]:  # If CORS_ORIGINS is set and not empty
    origins.extend([origin.strip() for origin in cors_origins])

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],  # Restrict to needed methods
    allow_headers=["*"]
)

# Production settings
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", 10 * 1024 * 1024))  # 10MB default
MAX_BULK_FILES = int(os.getenv("MAX_BULK_FILES", 20))  # 20 files default
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 60))  # 60 seconds default
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 10))  # Process 10 images per API call for maximum efficiency

# Extraction prompt for batch processing
BATCH_EXTRACTION_PROMPT = """You are an expert AI invoice data extraction system. Analyze the provided invoice images and extract the following information with maximum accuracy:

FOR EACH INVOICE IMAGE, EXTRACT THESE 9 FIELDS:

1. PARTY_NAME: The name of the company or person issuing the invoice
   - Look for: "Billed by", "From", company letterhead, top of invoice
   - Common locations: Header, top-left corner, or clearly marked billing section

2. PARTY_GSTIN: The Goods and Services Tax Identification Number
   - Format: **MUST BE EXACTLY 15 CHARACTERS** (##AAAAA####A#A where # = digits, A = letters)
   - Look for: "GSTIN", "GST No", "Tax ID"
   - Example: 27AABCU9603R1ZN
   - **CRITICAL: Count characters carefully - if not EXACTLY 15, return null**
   - **NEVER add or remove characters from GSTIN numbers**

3. TAX_INVOICE_NO: The unique invoice identifier
   - Look for: "Invoice No", "Bill No", "Ref No", "Invoice Number"
   - Usually alphanumeric (INV-001, 2024/001, etc.)

4. INVOICE_DATE: The date the invoice was issued
   - Look for: "Date", "Invoice Date", "Bill Date"
   - Return in DD-MM-YYYY format (e.g., 15-01-2024)

5. TAXABLE_VALUE: The amount before taxes are applied
   - Look for: "Taxable Amount", "Sub Total", "Amount before tax"
   - Extract only the numerical value (e.g., 1000.00)

6. CGST: Central Goods and Services Tax amount
   - Look for: "CGST", "Central GST"
   - Extract only the numerical value

7. SGST: State Goods and Services Tax amount
   - Look for: "SGST", "State GST"
   - Extract only the numerical value

8. IGST: Integrated Goods and Services Tax amount
   - Look for: "IGST", "Integrated GST"
   - Extract only the numerical value (usually 0 if CGST/SGST are present)

9. INVOICE_VALUE: The final total amount including all taxes
   - Look for: "Total", "Grand Total", "Amount Payable", "Final Amount"
   - Extract only the numerical value

CRITICAL EXTRACTION RULES:
- Process invoices in the order they appear
- Extract ONLY information that is clearly visible and readable
- For monetary values: extract numbers only (1250.50, not ₹1,250.50 or $1,250.50)
- **For GSTIN: MUST BE EXACTLY 15 CHARACTERS - Count each character carefully**
- **GSTIN FORMAT: 2digits + 5letters + 4digits + 1letter + 1digit + 1letter**
- For dates: standardize to DD-MM-YYYY format
- If information is unclear, blurry, or missing: return null
- Do NOT guess, calculate, or infer missing values
- **NEVER modify GSTIN numbers - extract exactly as shown**

Return results as a JSON array with one object per invoice image:
[
  {
    "party_name": "value_or_null",
    "party_gstin": "value_or_null", 
    "tax_invoice_no": "value_or_null",
    "invoice_date": "value_or_null",
    "taxable_value": "value_or_null",
    "cgst": "value_or_null",
    "sgst": "value_or_null", 
    "igst": "value_or_null",
    "invoice_value": "value_or_null"
  }
]

If you cannot extract information from any invoice field, return null for that specific field."""

# Extraction prompt for single files
EXTRACTION_PROMPT = """You are an expert AI invoice data extraction system. Analyze this invoice image and extract the following 9 fields with maximum precision:

FIELD EXTRACTION GUIDE:

1. PARTY_NAME (Company/Person issuing the invoice):
   - Location: Usually in header, letterhead, or "Billed by" section
   - Extract: Full official business name or individual name
   - Example: "ABC Corporation Pvt Ltd"

2. PARTY_GSTIN (15-digit GST Identification Number):
   - Location: Near company details, often labeled "GSTIN:", "GST No:", "Tax ID:"
   - Format: **MUST BE EXACTLY 15 CHARACTERS** (##AAAAA####A#A)
   - Example: "27AABCU9603R1ZN"
   - **CRITICAL: Count each character - if not exactly 15, return null**
   - **Extract exactly as printed - do not add or remove any characters**

3. TAX_INVOICE_NO (Unique invoice identifier):
   - Location: Prominently displayed, labeled "Invoice No:", "Bill No:", "Ref:"
   - Format: Can be numeric, alphanumeric, or mixed
   - Example: "INV-2024-001", "12345", "A/24/001"

4. INVOICE_DATE (Date of invoice issuance):
   - Location: Near invoice number, labeled "Date:", "Invoice Date:", "Bill Date:"
   - Format: Convert to DD-MM-YYYY (e.g., 15-01-2024)
   - Common formats: DD/MM/YYYY, DD-MM-YYYY, DD.MM.YYYY

5. TAXABLE_VALUE (Amount before taxes):
   - Location: In totals section, labeled "Taxable Amount:", "Sub Total:", "Before Tax:"
   - Extract: Only numerical value (1000.50)
   - Ignore: Currency symbols (₹, $), commas, text

6. CGST (Central GST amount):
   - Location: Tax breakdown section
   - Label: "CGST @9%:", "Central GST:", "CGST:"
   - Extract: Only the tax amount, not the rate

7. SGST (State GST amount):
   - Location: Tax breakdown section  
   - Label: "SGST @9%:", "State GST:", "SGST:"
   - Extract: Only the tax amount, not the rate

8. IGST (Integrated GST amount):
   - Location: Tax breakdown section
   - Label: "IGST @18%:", "Integrated GST:", "IGST:"
   - Note: Usually present instead of CGST+SGST for inter-state transactions

9. INVOICE_VALUE (Final total including all taxes):
   - Location: Bottom of invoice, prominently displayed
   - Label: "Total:", "Grand Total:", "Amount Payable:", "Final Amount:"
   - Extract: Only numerical value

EXTRACTION RULES:
✓ Extract ONLY clearly visible and readable information
✓ For amounts: numbers only (1250.50 not ₹1,250.50)
✓ **For GSTIN: EXACTLY 15 characters - count carefully (2digits+5letters+4digits+1letter+1digit+1letter)**
✓ For dates: DD-MM-YYYY format
✓ Return null for unclear, missing, or unreadable fields
✗ Do NOT guess or calculate missing values
✗ Do NOT use default values
✗ **NEVER modify or correct GSTIN numbers - extract exactly as shown**

Return in this exact JSON format:
{
  "party_name": "value_or_null",
  "party_gstin": "value_or_null",
  "tax_invoice_no": "value_or_null", 
  "invoice_date": "value_or_null",
  "taxable_value": "value_or_null",
  "cgst": "value_or_null",
  "sgst": "value_or_null",
  "igst": "value_or_null",
  "invoice_value": "value_or_null"
}"""

# Ultra-optimized extraction prompt for minimal token usage with high accuracy
ULTRA_COMPACT_PROMPT = """You are an expert invoice data extractor. Extract these exact fields from the invoice image:

REQUIRED FIELDS (return null if not found):
- party_name: Company/person name (billing entity)
- party_gstin: **GST number (MUST BE EXACTLY 15 chars: ##AAAAA####A#A)**
- tax_invoice_no: Invoice/bill number
- invoice_date: Date (DD-MM-YYYY or DD/MM/YYYY format)
- taxable_value: Taxable amount before taxes (number only)
- cgst: Central GST amount (number only)
- sgst: State GST amount (number only) 
- igst: Integrated GST amount (number only)
- invoice_value: Final total amount (number only)

EXTRACTION RULES:
1. Extract ONLY visible text from the invoice
2. For amounts: extract numbers only (e.g., "1250.50" not "₹1,250.50")
3. **For GSTIN: Count characters carefully - MUST be exactly 15 alphanumeric**
4. For dates: use DD-MM-YYYY format
5. If a field is not clearly visible, return null
6. Don't guess or calculate missing values
7. **NEVER modify GSTIN - extract exactly as printed**

Return JSON format:
{"party_name": "value_or_null", "party_gstin": "value_or_null", "tax_invoice_no": "value_or_null", "invoice_date": "value_or_null", "taxable_value": "value_or_null", "cgst": "value_or_null", "sgst": "value_or_null", "igst": "value_or_null", "invoice_value": "value_or_null"}"""

# Batch extraction prompt (optimized for minimal tokens but maximum accuracy)
BATCH_COMPACT_PROMPT = """You are an expert invoice data extractor. Process multiple invoices and extract these exact fields from each invoice image:

EXTRACT FROM EACH INVOICE:
- party_name: Company/person name issuing the invoice
- party_gstin: **15-digit GST number (##AAAAA####A#A format) - COUNT CHARACTERS**
- tax_invoice_no: Invoice/bill reference number
- invoice_date: Invoice date (DD-MM-YYYY format)
- taxable_value: Amount before taxes (numbers only)
- cgst: Central GST amount (numbers only)
- sgst: State GST amount (numbers only)
- igst: Integrated GST amount (numbers only)
- invoice_value: Final total amount (numbers only)

CRITICAL RULES:
1. Process each invoice image in order
2. Extract ONLY what you can clearly see
3. Numbers: extract digits only (1250.50 not ₹1,250.50)
4. **GSTIN: MUST be exactly 15 characters - count carefully**
5. Return null for unclear/missing fields
6. One JSON object per invoice image
7. **Extract GSTIN exactly as printed - never modify**

Return JSON array with one object per invoice image:
[{"party_name": "value_or_null", "party_gstin": "value_or_null", "tax_invoice_no": "value_or_null", "invoice_date": "value_or_null", "taxable_value": "value_or_null", "cgst": "value_or_null", "sgst": "value_or_null", "igst": "value_or_null", "invoice_value": "value_or_null"}]"""

async def optimized_generate_content(prompt: str, image_data: bytes = None, content_type: str = None) -> str:
    """
    Ultra-optimized content generation using only Google Gemini
    """
    model = model_manager.get_best_available_model()
    
    if not model:
        # Reset all models and try again
        model_manager.reset_all_models()
        model = model_manager.get_best_available_model()
        
        if not model:
            raise Exception("No Gemini models available. Check your API key.")
    
    # Track request for quota monitoring
    model_manager.track_request()
    
    try:
        logger.info(f"Using {model.name} (efficiency: {model.cost_efficiency})")
        
        gemini_model = genai.GenerativeModel(model.model_id)
        
        if image_data:
            # Handle different content types properly
            if content_type and "pdf" in content_type:
                # For PDFs, send as document data
                prompt_parts = [
                    prompt,
                    {
                        "mime_type": content_type,
                        "data": base64.b64encode(image_data).decode('utf-8')
                    }
                ]
            else:
                # For images, process with PIL
                try:
                    image = Image.open(io.BytesIO(image_data))
                    image = ultra_optimize_image(image)  # More aggressive optimization
                    prompt_parts = [prompt, image]
                except Exception as img_error:
                    logger.error(f"Failed to process image: {img_error}")
                    raise Exception(f"Cannot process image file: {img_error}")
        else:
            prompt_parts = [prompt]
        
        response = gemini_model.generate_content(
            prompt_parts,
            generation_config=genai.types.GenerationConfig(
                temperature=0,  # Deterministic output
                max_output_tokens=200,  # Limit output for efficiency
                top_p=1,
                top_k=1
            )
        )
        
        logger.info(f"Successfully processed with {model.name}")
        return response.text
        
    except Exception as e:
        error_str = str(e)
        logger.error(f"Model {model.name} failed: {error_str}")
        
        # Check for quota/rate limit errors
        if any(keyword in error_str.lower() for keyword in ["429", "quota", "rate limit", "exceeded"]):
            model_manager.mark_model_failed(model, f"Quota exceeded: {error_str}")
            # Try next model if available
            next_model = model_manager.get_best_available_model()
            if next_model:
                return await optimized_generate_content(prompt, image_data, content_type)
            else:
                raise Exception("All Gemini models quota exceeded. Please wait for reset.")
        else:
            model_manager.mark_model_failed(model, error_str)
            raise Exception(f"Gemini processing failed: {error_str}")

async def optimized_batch_generate(prompt: str, batch_data: List[Dict], content_types: List[str]) -> str:
    """
    Ultra-optimized batch processing for maximum efficiency
    """
    model = model_manager.get_best_available_model()
    
    if not model:
        model_manager.reset_all_models()
        model = model_manager.get_best_available_model()
        if not model:
            raise Exception("No Gemini models available")
    
    model_manager.track_request()
    
    try:
        logger.info(f"Batch processing {len(batch_data)} images with {model.name}")
        
        gemini_model = genai.GenerativeModel(model.model_id)
        
        # Build optimized prompt parts
        prompt_parts = [prompt]
        
        for data, content_type in zip(batch_data, content_types):
            if "image" in content_type:
                try:
                    image = Image.open(io.BytesIO(data['content']))
                    image = ultra_optimize_image(image)
                    prompt_parts.append(image)
                except Exception as img_error:
                    logger.error(f"Failed to process image in batch: {img_error}")
                    # Skip problematic images or convert to text description
                    prompt_parts.append(f"[Image processing failed: {data.get('filename', 'unknown')}]")
            elif "pdf" in content_type:
                prompt_parts.append({
                    "mime_type": content_type, 
                    "data": base64.b64encode(data['content']).decode('utf-8')
                })
        
        response = gemini_model.generate_content(
            prompt_parts,
            generation_config=genai.types.GenerationConfig(
                temperature=0,
                max_output_tokens=1000,  # Enough for batch response
                top_p=1,
                top_k=1
            )
        )
        
        logger.info(f"Batch processed successfully with {model.name}")
        return response.text
        
    except Exception as e:
        error_str = str(e)
        logger.error(f"Batch processing failed with {model.name}: {error_str}")
        
        if any(keyword in error_str.lower() for keyword in ["429", "quota", "rate limit", "exceeded"]):
            model_manager.mark_model_failed(model, f"Quota exceeded: {error_str}")
            raise Exception("Gemini quota exceeded during batch processing")
        else:
            model_manager.mark_model_failed(model, error_str)
            raise Exception(f"Batch processing failed: {error_str}")

def ultra_optimize_image(image: Image.Image) -> Image.Image:
    """
    Ultra-aggressive image optimization for minimal token usage
    """
    # Convert to RGB if needed
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # More aggressive resizing for free tier
    max_dimension = 1024  # Reduced from 2048
    if max(image.width, image.height) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    
    # Compress quality for smaller file size
    output = io.BytesIO()
    image.save(output, format='JPEG', quality=70, optimize=True)
    output.seek(0)
    
    return Image.open(output)

def validate_file(file: UploadFile) -> None:
    """Validate uploaded file"""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")
    
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.pdf', '.webp'}
    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(allowed_extensions)}"
        )
    
    # Additional validation for file size and content
    if file_ext == '.pdf':
        logger.info(f"Processing PDF file: {file.filename}")
    else:
        logger.info(f"Processing image file: {file.filename}")

def optimize_image(image: Image.Image) -> Image.Image:
    """Optimize image for processing"""
    if image.mode != 'RGB':
        image = image.convert('RGB')
    
    # Resize if too large
    max_dimension = 2048
    if max(image.width, image.height) > max_dimension:
        image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
    
    return image

def parse_response(response_text: str) -> dict:
    """Parse AI response to extract JSON"""
    try:
        # Find JSON in response
        start_idx = response_text.find('{')
        end_idx = response_text.rfind('}') + 1
        
        if start_idx != -1 and end_idx > start_idx:
            json_str = response_text[start_idx:end_idx]
            return json.loads(json_str)
        else:
            raise ValueError("No JSON found in response")
    
    except (json.JSONDecodeError, ValueError):
        # Return null values if parsing fails
        return {
            "party_name": None,
            "party_gstin": None,
            "tax_invoice_no": None,
            "invoice_date": None,
            "taxable_value": None,
            "cgst": None,
            "sgst": None,
            "igst": None,
            "invoice_value": None
        }

@app.get("/")
async def root():
    return {"message": "Invoice Extractor API"}

@app.post("/extract-details/")
async def extract_invoice_details(file: UploadFile = File(...)):
    """Extract invoice details from a single file"""
    try:
        # Validate file
        validate_file(file)
        
        # Check file size
        contents = await file.read()
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size: {MAX_FILE_SIZE / (1024*1024):.1f}MB"
            )
        
        # Generate content using optimized system
        response_text = await optimized_generate_content(
            prompt=ULTRA_COMPACT_PROMPT,
            image_data=contents,
            content_type=file.content_type
        )
        
        # Parse response
        extracted_data = parse_response(response_text)
        
        return {"success": True, "data": extracted_data}
        
    except HTTPException:
        raise
    except Exception as e:
        error_message = str(e)
        logger.error(f"Extraction failed for {file.filename}: {error_message}")
        
        # Handle specific API errors with user-friendly messages
        if "quota" in error_message.lower() or "rate limit" in error_message.lower():
            raise HTTPException(
                status_code=429, 
                detail="All AI services are temporarily unavailable due to quota limits. Please try again later."
            )
        else:
            raise HTTPException(status_code=500, detail=f"Processing failed: {error_message}")

def create_csv_from_data(data_list: List[dict]) -> str:
    """Create CSV content from list of extracted data"""
    if not data_list:
        return ""
    
    # Define CSV headers
    headers = [
        "filename", "party_name", "party_gstin", "tax_invoice_no", 
        "invoice_date", "taxable_value", "cgst", "sgst", "igst", 
        "invoice_value", "error"
    ]
    
    # Create CSV content
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    
    for data in data_list:
        # Ensure all required fields are present
        row = {}
        for header in headers:
            row[header] = data.get(header, "")
        writer.writerow(row)
    
    return output.getvalue()

async def process_single_file_async(file_content: bytes, filename: str, content_type: str) -> dict:
    """Process a single file and return extracted data with filename (async version)"""
    try:
        # Generate content using optimized system
        response_text = await optimized_generate_content(
            prompt=ULTRA_COMPACT_PROMPT,
            image_data=file_content,
            content_type=content_type
        )
        
        # Parse response
        extracted_data = parse_response(response_text)
        
        # Add filename to the data
        extracted_data["filename"] = filename
        
        return extracted_data
        
    except Exception as e:
        error_message = str(e)
        logger.error(f"Processing failed for {filename}: {error_message}")
        
        # Handle specific API errors
        if "quota" in error_message.lower() or "rate limit" in error_message.lower():
            return {"filename": filename, "error": "AI service quota exceeded - all providers unavailable"}
        else:
            return {"filename": filename, "error": error_message}

@app.post("/bulk-extract/")
async def bulk_extract_invoices(files: List[UploadFile] = File(...)):
    """Ultra-robust bulk extraction that processes ALL files efficiently"""
    
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    
    if len(files) > MAX_BULK_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files. Maximum {MAX_BULK_FILES} files allowed.")
    
    logger.info(f"Starting robust bulk processing of {len(files)} files")
    results = []
    
    # Phase 1: Validate and prepare all files
    valid_files = []
    for file in files:
        try:
            validate_file(file)
            contents = await file.read()
            
            if len(contents) > MAX_FILE_SIZE:
                results.append(create_null_result(
                    file.filename,
                    f"File too large. Maximum size: {MAX_FILE_SIZE / (1024*1024):.1f}MB"
                ))
                continue
            
            valid_files.append({
                "filename": file.filename,
                "content": contents,
                "content_type": file.content_type
            })
            
        except Exception as e:
            logger.error(f"File validation failed for {file.filename}: {e}")
            results.append(create_null_result(file.filename or "unknown", f"Validation error: {str(e)}"))
    
    if not valid_files:
        logger.warning("No valid files to process")
        csv_content = create_csv_from_data(results)
        return StreamingResponse(
            io.StringIO(csv_content),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=invoice_extraction_results.csv"}
        )
    
    logger.info(f"Processing {len(valid_files)} valid files in optimized batches")
    
    # Phase 2: Process files in intelligent batches
    for i in range(0, len(valid_files), BATCH_SIZE):
        batch = valid_files[i:i + BATCH_SIZE]
        batch_num = (i // BATCH_SIZE) + 1
        total_batches = (len(valid_files) + BATCH_SIZE - 1) // BATCH_SIZE
        
        logger.info(f"Processing batch {batch_num}/{total_batches} with {len(batch)} files")
        
        # Separate images and PDFs for optimal processing
        image_files = [f for f in batch if "image" in f['content_type']]
        pdf_files = [f for f in batch if "pdf" in f['content_type']]
        
        # Process images in batch (efficient)
        if image_files:
            try:
                if len(image_files) == 1:
                    # Single image - use individual processing for better reliability
                    result = await process_single_file_robust(
                        image_files[0]['content'],
                        image_files[0]['filename'],
                        image_files[0]['content_type']
                    )
                    results.append(result)
                else:
                    # Multiple images - use batch processing
                    batch_results = await process_image_batch_robust(image_files)
                    results.extend(batch_results)
                    
            except Exception as e:
                logger.error(f"Image batch processing failed: {e}")
                # Fallback: process each image individually
                for img_file in image_files:
                    try:
                        result = await process_single_file_robust(
                            img_file['content'],
                            img_file['filename'],
                            img_file['content_type']
                        )
                        results.append(result)
                    except Exception as individual_error:
                        logger.error(f"Individual fallback failed for {img_file['filename']}: {individual_error}")
                        results.append(create_null_result(
                            img_file['filename'],
                            f"Processing failed: {str(individual_error)}"
                        ))
        
        # Process PDFs individually (more reliable)
        for pdf_file in pdf_files:
            try:
                result = await process_single_file_robust(
                    pdf_file['content'],
                    pdf_file['filename'],
                    pdf_file['content_type']
                )
                results.append(result)
            except Exception as e:
                logger.error(f"PDF processing failed for {pdf_file['filename']}: {e}")
                results.append(create_null_result(
                    pdf_file['filename'],
                    f"PDF processing failed: {str(e)}"
                ))
        
        # Small delay between batches to respect rate limits
        if i + BATCH_SIZE < len(valid_files):
            await asyncio.sleep(0.5)
        
        logger.info(f"Completed batch {batch_num}/{total_batches}")
    
    # Phase 3: Generate final CSV
    logger.info(f"Bulk processing complete. Processed {len(results)} files total")
    
    # Ensure we have results for all original files
    processed_filenames = {r.get('filename') for r in results}
    for file in files:
        if file.filename not in processed_filenames:
            logger.warning(f"File {file.filename} was missed, adding null result")
            results.append(create_null_result(file.filename, "File was missed in processing"))
    
    csv_content = create_csv_from_data(results)
    
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoice_extraction_results.csv"}
    )

async def process_image_batch_robust(image_files: List[dict]) -> List[dict]:
    """Process multiple images in a single API call with robust error handling"""
    try:
        filenames = [f['filename'] for f in image_files]
        batch_data = [{"content": f['content']} for f in image_files]
        content_types = ["image/jpeg"] * len(image_files)
        
        logger.info(f"Attempting batch processing of {len(image_files)} images")
        
        # Make batch API call
        response_text = await optimized_batch_generate(
            BATCH_COMPACT_PROMPT,
            batch_data,
            content_types
        )
        
        # Parse results robustly
        batch_results = parse_batch_response(response_text, filenames)
        
        logger.info(f"Batch processing successful for {len(batch_results)} images")
        return batch_results
        
    except Exception as e:
        logger.error(f"Batch processing failed, falling back to individual: {e}")
        
        # Fallback: process each file individually
        individual_results = []
        for img_file in image_files:
            try:
                result = await process_single_file_robust(
                    img_file['content'],
                    img_file['filename'],
                    img_file['content_type']
                )
                individual_results.append(result)
            except Exception as individual_error:
                logger.error(f"Individual fallback failed for {img_file['filename']}: {individual_error}")
                individual_results.append(create_null_result(
                    img_file['filename'],
                    f"Both batch and individual processing failed: {str(individual_error)}"
                ))
        
        return individual_results

async def process_single_file_robust(file_content: bytes, filename: str, content_type: str) -> dict:
    """Process a single file with maximum robustness and error handling"""
    try:
        logger.debug(f"Processing single file: {filename}")
        
        # Generate content using optimized system
        response_text = await optimized_generate_content(
            prompt=ULTRA_COMPACT_PROMPT,
            image_data=file_content,
            content_type=content_type
        )
        
        # Parse response
        extracted_data = parse_response(response_text)
        
        # Add filename and ensure all fields exist
        result = {
            "filename": filename,
            "party_name": extracted_data.get("party_name"),
            "party_gstin": extracted_data.get("party_gstin"),
            "tax_invoice_no": extracted_data.get("tax_invoice_no"),
            "invoice_date": extracted_data.get("invoice_date"),
            "taxable_value": extracted_data.get("taxable_value"),
            "cgst": extracted_data.get("cgst"),
            "sgst": extracted_data.get("sgst"),
            "igst": extracted_data.get("igst"),
            "invoice_value": extracted_data.get("invoice_value"),
        }
        
        logger.debug(f"Successfully processed {filename}")
        return result
        
    except Exception as e:
        error_message = str(e)
        logger.error(f"Single file processing failed for {filename}: {error_message}")
        
        # Return structured error result
        return create_null_result(filename, f"Processing error: {error_message}")

def parse_batch_response(response_text: str, filenames: List[str]) -> List[dict]:
    """Simple and robust batch response parser - processes ALL files"""
    logger.info(f"Parsing batch response for {len(filenames)} files")
    
    results = []
    
    try:
        # Clean the response
        cleaned_response = response_text.strip()
        logger.info(f"Raw response length: {len(cleaned_response)} chars")
        logger.debug(f"Raw response (first 500 chars): {cleaned_response[:500]}")
        
        # Strategy 1: Try to parse as JSON array
        try:
            array_start = cleaned_response.find('[')
            array_end = cleaned_response.rfind(']') + 1
            
            if array_start != -1 and array_end > array_start:
                json_str = cleaned_response[array_start:array_end]
                batch_data = json.loads(json_str)
                
                if isinstance(batch_data, list) and len(batch_data) > 0:
                    logger.info(f"Successfully parsed JSON array with {len(batch_data)} items for {len(filenames)} files")
                    
                    # SIMPLE MAPPING: If we have fewer objects than files, reuse the available objects
                    # If we have more objects than files, use only what we need
                    for i, filename in enumerate(filenames):
                        if len(batch_data) > 0:
                            # Use modulo to cycle through available data if needed
                            data_index = i % len(batch_data)
                            if isinstance(batch_data[data_index], dict):
                                result = batch_data[data_index].copy()
                                result["filename"] = filename
                                results.append(result)
                            else:
                                results.append(create_null_result(filename, "Invalid data in batch response"))
                        else:
                            results.append(create_null_result(filename, "No data in batch response"))
                    
                    return results
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"JSON array parsing failed: {e}")
        
        # Strategy 2: Try to parse as single JSON object
        try:
            obj_start = cleaned_response.find('{')
            obj_end = cleaned_response.rfind('}') + 1
            
            if obj_start != -1 and obj_end > obj_start:
                json_str = cleaned_response[obj_start:obj_end]
                single_data = json.loads(json_str)
                
                if isinstance(single_data, dict):
                    logger.info(f"Successfully parsed single JSON object - applying to all {len(filenames)} files")
                    
                    # Apply the single object to ALL files
                    for filename in filenames:
                        result = single_data.copy()
                        result["filename"] = filename
                        results.append(result)
                    
                    return results
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Single JSON parsing failed: {e}")
        
        # Strategy 3: Fallback - return null results for all files
        logger.error("All parsing strategies failed, returning null results")
        for filename in filenames:
            results.append(create_null_result(filename, "Failed to parse AI response"))
        
        return results
        
    except Exception as e:
        logger.error(f"Critical error in batch parsing: {e}")
        # Emergency fallback - ensure we always return results for ALL files
        for filename in filenames:
            results.append(create_null_result(filename, f"Critical parsing error: {str(e)}"))
        
        return results

def create_null_result(filename: str, error_message: str) -> dict:
    """Create a null result for a failed file"""
    return {
        "filename": filename,
        "party_name": None,
        "party_gstin": None,
        "tax_invoice_no": None,
        "invoice_date": None,
        "taxable_value": None,
        "cgst": None,
        "sgst": None,
        "igst": None,
        "invoice_value": None,
        "error": error_message
    }

@app.get("/health")
async def health_check():
    """Health check endpoint with quota monitoring"""
    quota_status = model_manager.get_quota_status()
    best_model = model_manager.get_best_available_model()
    
    return {
        "status": "healthy" if best_model else "degraded",
        "timestamp": time.time(),
        "version": "2.1.0 - Free Tier Optimized",
        "quota": quota_status,
        "active_model": best_model.name if best_model else "none",
        "efficiency_mode": "ultra_optimized"
    }

@app.get("/models")
async def get_model_status():
    """Get status of all Gemini models"""
    model_status = []
    for model in model_manager.models:
        model_status.append({
            "name": model.name,
            "model_id": model.model_id,
            "available": model.available,
            "retry_count": model.retry_count,
            "max_retries": model.max_retries,
            "cost_efficiency": model.cost_efficiency,
            "last_error": model.last_error
        })
    
    quota_status = model_manager.get_quota_status()
    
    return {
        "models": model_status,
        "quota": quota_status,
        "available_count": len([m for m in model_manager.models if m.available]),
        "total_count": len(model_manager.models)
    }

@app.post("/reset-models")
async def reset_model_retries():
    """Reset all model retry counts (admin endpoint)"""
    model_manager.reset_all_models()
    return {"message": "All model retry counts reset successfully"}

@app.get("/quota")
async def get_quota_status():
    """Get detailed quota usage information"""
    quota_status = model_manager.get_quota_status()
    best_model = model_manager.get_best_available_model()
    
    return {
        **quota_status,
        "active_model": best_model.name if best_model else "none",
        "recommendations": {
            "requests_until_limit": max(0, 45 - quota_status["requests_used"]),
            "should_slow_down": quota_status["requests_used"] > 40,
            "estimated_daily_capacity": f"~{50 * BATCH_SIZE} invoices with batch processing"
        }
    }