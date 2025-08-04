import google.generativeai as genai
import io
import os
import logging
import time
import asyncio
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
            ("Gemini 1.0 Pro", "gemini-1.0-pro", 3),               # Fallback
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
BATCH_SIZE = int(os.getenv("BATCH_SIZE", 5))  # Process 5 images per API call to optimize quota usage

# Extraction prompt for batch processing
BATCH_EXTRACTION_PROMPT = """
I will provide you with multiple invoice images. For each invoice, extract the following information:

1. PARTY_NAME (company/person name)
2. PARTY_GSTIN (GST identification number)
3. TAX_INVOICE_NO (invoice number)
4. INVOICE_DATE (date of invoice)
5. TAXABLE_VALUE (taxable amount)
6. CGST (Central GST amount)
7. SGST (State GST amount)
8. IGST (Integrated GST amount)
9. INVOICE_VALUE (total invoice value)

Return the results as a JSON array where each object represents one invoice in the same order as provided. Use this exact format:
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

If you cannot extract information from any invoice, return null values for that invoice's fields.
"""

# Extraction prompt for single files
EXTRACTION_PROMPT = """
Extract the following information from this invoice:

1. PARTY_NAME (company/person name)
2. PARTY_GSTIN (GST identification number)
3. TAX_INVOICE_NO (invoice number)
4. INVOICE_DATE (date of invoice)
5. TAXABLE_VALUE (taxable amount)
6. CGST (Central GST amount)
7. SGST (State GST amount)
8. IGST (Integrated GST amount)
9. INVOICE_VALUE (total invoice value)

Return the result in this exact JSON format:
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
"""

# Ultra-optimized extraction prompt for minimal token usage
ULTRA_COMPACT_PROMPT = """Extract: party_name, party_gstin, tax_invoice_no, invoice_date, taxable_value, cgst, sgst, igst, invoice_value as JSON"""

# Batch extraction prompt (optimized for minimal tokens)
BATCH_COMPACT_PROMPT = """Extract from each invoice: party_name, party_gstin, tax_invoice_no, invoice_date, taxable_value, cgst, sgst, igst, invoice_value. Return JSON array"""

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
            image = Image.open(io.BytesIO(image_data))
            image = ultra_optimize_image(image)  # More aggressive optimization
            prompt_parts = [prompt, image]
        elif content_type and "pdf" in content_type:
            prompt_parts = [prompt, {"mime_type": content_type, "data": image_data}]
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
                image = Image.open(io.BytesIO(data['content']))
                image = ultra_optimize_image(image)
                prompt_parts.append(image)
            elif "pdf" in content_type:
                prompt_parts.append({"mime_type": content_type, "data": data['content']})
        
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
    """Extract details from multiple invoice files and return CSV (optimized for quota usage)"""
    
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    
    if len(files) > MAX_BULK_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files. Maximum {MAX_BULK_FILES} files allowed.")
    
    results = []
    
    # Prepare all files first
    file_data = []
    for file in files:
        try:
            validate_file(file)
            contents = await file.read()
            
            if len(contents) > MAX_FILE_SIZE:
                results.append({
                    "filename": file.filename,
                    "error": f"File too large. Maximum size: {MAX_FILE_SIZE / (1024*1024):.1f}MB"
                })
                continue
            
            file_data.append({
                "filename": file.filename,
                "content": contents,
                "content_type": file.content_type
            })
            
        except Exception as e:
            results.append({
                "filename": file.filename or "unknown",
                "error": str(e)
            })
    
    if not file_data:
        # All files had errors
        csv_content = create_csv_from_data(results)
        return StreamingResponse(
            io.StringIO(csv_content),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=invoice_extraction_results.csv"}
        )
    
    # Process files in batches to optimize API quota usage
    logger.info(f"Processing {len(file_data)} files in batches of {BATCH_SIZE}")
    
    for i in range(0, len(file_data), BATCH_SIZE):
        batch = file_data[i:i + BATCH_SIZE]
        
        try:
            if len(batch) == 1:
                # Single file - use individual processing
                result = await process_single_file_async(
                    batch[0]['content'], 
                    batch[0]['filename'], 
                    batch[0]['content_type']
                )
                results.append(result)
            else:
                # Multiple files - use batch processing
                batch_data = [{"content": item['content']} for item in batch]
                content_types = [item['content_type'] for item in batch]
                
                # Filter out PDFs for now (batch processing works best with images)
                image_batch = []
                image_filenames = []
                
                for item in batch:
                    if "image" in item['content_type']:
                        image_batch.append({"content": item['content']})
                        image_filenames.append(item['filename'])
                    else:
                        # Process PDFs individually
                        pdf_result = await process_single_file_async(
                            item['content'], 
                            item['filename'], 
                            item['content_type']
                        )
                        results.append(pdf_result)
                
                if image_batch:
                    try:
                        # Process images in batch using optimized system
                        response_text = await optimized_batch_generate(
                            BATCH_COMPACT_PROMPT,
                            image_batch,
                            ["image/jpeg"] * len(image_batch)  # Simplified for now
                        )
                        
                        # Parse batch response
                        batch_results = parse_batch_response(response_text, image_filenames)
                        results.extend(batch_results)
                        
                        logger.info(f"Successfully processed batch of {len(image_batch)} images in single API call")
                        
                    except Exception as e:
                        logger.error(f"Batch processing failed, falling back to individual: {e}")
                        # Fallback to individual processing
                        for item in batch:
                            if "image" in item['content_type']:
                                individual_result = await process_single_file_async(
                                    item['content'], 
                                    item['filename'], 
                                    item['content_type']
                                )
                                results.append(individual_result)
                                
        except Exception as e:
            logger.error(f"Batch processing error: {e}")
            # Add error entries for this batch
            for item in batch:
                results.append({
                    "filename": item['filename'],
                    "error": f"Batch processing failed: {str(e)}"
                })
        
        # Small delay between batches
        if i + BATCH_SIZE < len(file_data):
            await asyncio.sleep(1)
    
    # Create CSV content
    csv_content = create_csv_from_data(results)
    
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoice_extraction_results.csv"}
    )

def parse_batch_response(response_text: str, filenames: List[str]) -> List[dict]:
    """Parse batch AI response to extract JSON array"""
    try:
        # Find JSON array in response
        start_idx = response_text.find('[')
        end_idx = response_text.rfind(']') + 1
        
        if start_idx != -1 and end_idx > start_idx:
            json_str = response_text[start_idx:end_idx]
            batch_data = json.loads(json_str)
            
            # Add filenames to each result
            results = []
            for i, data in enumerate(batch_data):
                if i < len(filenames):
                    data["filename"] = filenames[i]
                    results.append(data)
                else:
                    # More results than filenames
                    results.append({
                        "filename": f"unknown_{i}",
                        "error": "Filename mapping error",
                        **data
                    })
            
            # If fewer results than filenames, add error entries
            for i in range(len(batch_data), len(filenames)):
                results.append({
                    "filename": filenames[i],
                    "error": "No data extracted from batch response"
                })
            
            return results
        else:
            raise ValueError("No JSON array found in batch response")
    
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse batch response: {e}")
        # Return null values for all files in batch
        results = []
        for filename in filenames:
            results.append({
                "filename": filename,
                "party_name": None, "party_gstin": None, "tax_invoice_no": None,
                "invoice_date": None, "taxable_value": None, "cgst": None,
                "sgst": None, "igst": None, "invoice_value": None,
                "error": "Failed to parse batch response"
            })
        return results

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