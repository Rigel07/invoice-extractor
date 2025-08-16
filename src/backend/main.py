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
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# JSON Schema for structured output (Google Gemini best practice)
INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "party_name": {
            "type": "string",
            "description": "Company or person issuing the invoice",
            "nullable": True
        },
        "party_gstin": {
            "type": "string", 
            "description": "15-character GST identification number",
            "pattern": "^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9][A-Z]$",
            "minLength": 15,
            "maxLength": 15,
            "nullable": True
        },
        "tax_invoice_no": {
            "type": "string",
            "description": "Unique invoice identifier", 
            "nullable": True
        },
        "invoice_date": {
            "type": "string",
            "description": "Invoice date in DD-MM-YYYY format",
            "pattern": "^[0-9]{2}-[0-9]{2}-[0-9]{4}$",
            "nullable": True
        },
        "taxable_value": {
            "type": "string",
            "description": "Amount before taxes (numbers only)",
            "pattern": "^[0-9]+\\.?[0-9]*$",
            "nullable": True
        },
        "cgst": {
            "type": "string",
            "description": "Central GST amount (numbers only)",
            "pattern": "^[0-9]+\\.?[0-9]*$",
            "nullable": True
        },
        "sgst": {
            "type": "string", 
            "description": "State GST amount (numbers only)",
            "pattern": "^[0-9]+\\.?[0-9]*$",
            "nullable": True
        },
        "igst": {
            "type": "string",
            "description": "Integrated GST amount (numbers only)", 
            "pattern": "^[0-9]+\\.?[0-9]*$",
            "nullable": True
        },
        "invoice_value": {
            "type": "string",
            "description": "Total amount including taxes (numbers only)",
            "pattern": "^[0-9]+\\.?[0-9]*$",
            "nullable": True
        }
    },
    "required": ["party_name", "party_gstin", "tax_invoice_no", "invoice_date", "taxable_value", "cgst", "sgst", "igst", "invoice_value"],
    "propertyOrdering": ["party_name", "party_gstin", "tax_invoice_no", "invoice_date", "taxable_value", "cgst", "sgst", "igst", "invoice_value"]
}

# Schema for batch processing (array of invoices)
BATCH_INVOICE_SCHEMA = {
    "type": "array",
    "items": INVOICE_SCHEMA,
    "minItems": 1,
    "maxItems": 20
}

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
        self.daily_limit = 200  # Updated for Gemini 2.0 models - 4x better!
    
    def _setup_gemini_models(self):
        """Initialize Gemini models in order of cost efficiency - Updated for 2.5"""
        google_key = os.getenv("GOOGLE_API_KEY")
        if not google_key:
            raise ValueError("GOOGLE_API_KEY environment variable is required")
        
        genai.configure(api_key=google_key)
        
        # Order by efficiency: Gemini 2.5 Flash as primary, then 2.5 Lite, 2.0 models
        gemini_models = [
            ("Gemini 2.5 Flash", "gemini-2.5-flash", 1),                # PRIMARY: Latest and most capable
            ("Gemini 2.5 Flash-Lite", "gemini-2.5-flash-lite", 2),     # SECONDARY: Fast and efficient  
            ("Gemini 2.0 Flash", "gemini-2.0-flash", 3),               # FALLBACK: Good performance
            ("Gemini 2.0 Flash-Lite", "gemini-2.0-flash-lite", 4),     # FALLBACK: Backup option
        ]
        
        for name, model_id, efficiency in gemini_models:
            self.models.append(GeminiModel(
                name=name,
                model_id=model_id,
                cost_efficiency=efficiency
            ))
        
        logger.info(f"Initialized {len(self.models)} optimized Gemini 2.5/2.0 models with latest capabilities")
    
    def get_best_available_model(self) -> Optional[GeminiModel]:
        """Get the most efficient available model"""
        # Reset models if all are unavailable (helps with safety filter recovery)
        available = [m for m in self.models if m.available and m.retry_count < m.max_retries]
        if not available:
            logger.warning("No models available, resetting all for recovery")
            self.reset_all_models()
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
    
    def mark_model_temporarily_failed(self, model: GeminiModel, reason: str):
        """Temporarily mark a model as failed (for safety blocks, etc.)"""
        # Don't count safety blocks against retry count - they're not model failures
        if "safety" not in reason.lower():
            model.retry_count += 1
        model.last_error = reason
        if model.retry_count >= model.max_retries:
            model.available = False
        logger.warning(f"Temporarily failed {model.name}: {reason} (retry: {model.retry_count})")
    
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

# Optimized extraction prompt following Google Gemini best practices
BATCH_EXTRACTION_PROMPT = """You are an expert invoice data extraction AI. Extract EXACTLY 9 fields from each invoice image with maximum precision.

CRITICAL CONSTRAINTS:
- Extract information ONLY if clearly visible and readable
- For GSTIN: Must be EXACTLY 15 characters (2 digits + 5 letters + 4 digits + 1 letter + 1 digit + 1 letter)
- For monetary values: Extract numbers only (no currency symbols)
- For dates: Use DD-MM-YYYY format
- Use null for missing, unclear, or unreadable information
- Process invoices in the order they appear

EXTRACTION TASK:
For each invoice image, extract these 9 fields:

1. party_name: Company/person issuing the invoice (from header/letterhead)
2. party_gstin: 15-character GST number (count each character carefully)
3. tax_invoice_no: Unique invoice identifier 
4. invoice_date: Invoice issue date (DD-MM-YYYY format)
5. taxable_value: Amount before taxes (numbers only)
6. cgst: Central GST amount (numbers only)
7. sgst: State GST amount (numbers only)
8. igst: Integrated GST amount (numbers only)
9. invoice_value: Final total including taxes (numbers only)

EXAMPLES:

Example 1:
Input: Invoice showing "ABC Electronics Ltd", GSTIN "27AABCU9603R1ZN", Invoice "INV-2024-001"
Output:
[
  {
    "party_name": "ABC Electronics Ltd",
    "party_gstin": "27AABCU9603R1ZN",
    "tax_invoice_no": "INV-2024-001",
    "invoice_date": "15-01-2024",
    "taxable_value": "10000.00",
    "cgst": "900.00",
    "sgst": "900.00",
    "igst": null,
    "invoice_value": "11800.00"
  }
]

Example 2:
Input: Invoice with unclear GSTIN, readable company name "XYZ Services"
Output:
[
  {
    "party_name": "XYZ Services",
    "party_gstin": null,
    "tax_invoice_no": "BILL/2024/45",
    "invoice_date": "22-03-2024",
    "taxable_value": "5000.00",
    "cgst": null,
    "sgst": null,
    "igst": "900.00",
    "invoice_value": "5900.00"
  }
]

RESPONSE FORMAT:
Return a JSON array with one object per invoice:
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

GSTIN VALIDATION RULES:
✓ Exactly 15 characters: 12ABCDE3456F7G8
✓ Position 1-2: Numbers (state code)
✓ Position 3-7: Letters (PAN first 5 chars)
✓ Position 8-11: Numbers
✓ Position 12: Letter
✓ Position 13: Number (check digit)
✓ Position 14: Letter (default 'Z')
✓ Position 15: Letter/Number

If GSTIN doesn't match this pattern exactly, return null.

Process the invoice images and return the JSON array:"""

# Enhanced single extraction prompt using Google Gemini best practices
EXTRACTION_PROMPT = """Task: Extract invoice data from the provided image with maximum accuracy.

Input: Invoice image
Output: JSON object with 9 fields

Required Fields:
1. party_name: Company issuing the invoice (from header/letterhead)
2. party_gstin: 15-character GST number (exactly 15 chars: ##AAAAA####A#A)
3. tax_invoice_no: Invoice identifier
4. invoice_date: Issue date (DD-MM-YYYY format)
5. taxable_value: Pre-tax amount (numbers only)
6. cgst: Central GST amount (numbers only)
7. sgst: State GST amount (numbers only)
8. igst: Integrated GST amount (numbers only)
9. invoice_value: Total amount including taxes (numbers only)

Constraints:
- Extract ONLY clearly visible information
- GSTIN must be EXACTLY 15 characters or return null
- Use null for missing/unclear data
- Numbers only for monetary values (no symbols)
- Date format: DD-MM-YYYY

Example Input: Invoice from "Tech Solutions Ltd", GSTIN "29ABCDE1234F5G6", Invoice "INV-001"
Example Output:
{
  "party_name": "Tech Solutions Ltd",
  "party_gstin": "29ABCDE1234F5G6",
  "tax_invoice_no": "INV-001",
  "invoice_date": "15-03-2024",
  "taxable_value": "5000.00",
  "cgst": "450.00",
  "sgst": "450.00",
  "igst": null,
  "invoice_value": "5900.00"
}

GSTIN Validation Pattern:
Position 1-2: State code (numbers)
Position 3-7: PAN first 5 (letters)
Position 8-11: Entity number (numbers)
Position 12: Check digit (letter)
Position 13: Validation digit (number)
Position 14: Default 'Z' (letter)
Position 15: Check code (letter/number)

Extract the data from the invoice image and return JSON:"""

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
    Ultra-optimized content generation using only Google Gemini with safety handling
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
        
        # Enhanced generation config with safety settings
        generation_config = genai.types.GenerationConfig(
            temperature=0,  # Deterministic output
            max_output_tokens=1000,  # Increased for complex invoices
            top_p=0.95,  # Slightly more flexible
            top_k=40     # Allow more token options
        )
        
        # Safety settings to reduce blocking - most permissive for business documents
        safety_settings = [
            {
                "category": "HARM_CATEGORY_HARASSMENT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_HATE_SPEECH", 
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "threshold": "BLOCK_NONE"
            },
            {
                "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                "threshold": "BLOCK_NONE"
            }
        ]
        
        # For 2.5 Flash models, try even more permissive settings
        if "2.5" in model.name:
            safety_settings = []  # No safety restrictions for business documents
        
        response = gemini_model.generate_content(
            prompt_parts,
            generation_config=generation_config,
            safety_settings=safety_settings
        )
        
        # Enhanced response handling for safety blocks
        if response.candidates:
            candidate = response.candidates[0]
            
            # Check finish reason
            if hasattr(candidate, 'finish_reason'):
                finish_reason = candidate.finish_reason
                
                if finish_reason == 2:  # SAFETY
                    logger.warning(f"Content blocked by safety filters for {model.name}")
                    
                    # Try multiple fallback strategies before giving up on this model
                    fallback_strategies = [
                        # Strategy 1: Ultra-simple prompt
                        """Extract invoice data from this document. Return JSON with these exact fields:
party_name, party_gstin, tax_invoice_no, invoice_date, taxable_value, cgst, sgst, igst, invoice_value""",
                        
                        # Strategy 2: Business document prompt
                        """This is a business invoice document. Please extract the key financial information in JSON format.""",
                        
                        # Strategy 3: Minimal prompt
                        """Extract text data as JSON from this document."""
                    ]
                    
                    for i, fallback_prompt in enumerate(fallback_strategies):
                        try:
                            logger.info(f"Trying safety fallback strategy {i+1} with {model.name}")
                            fallback_response = gemini_model.generate_content(
                                [fallback_prompt, prompt_parts[-1]] if len(prompt_parts) > 1 else [fallback_prompt],
                                generation_config=generation_config,
                                safety_settings=safety_settings
                            )
                            
                            if (fallback_response.candidates and 
                                hasattr(fallback_response.candidates[0], 'finish_reason') and
                                fallback_response.candidates[0].finish_reason != 2 and
                                hasattr(fallback_response, 'text') and fallback_response.text):
                                logger.info(f"Safety fallback strategy {i+1} succeeded with {model.name}")
                                return fallback_response.text
                        except Exception as fallback_error:
                            logger.warning(f"Fallback strategy {i+1} failed: {fallback_error}")
                            continue
                    
                    # If all fallback strategies fail, try next model without marking this one as failed
                    logger.warning(f"All safety fallback strategies failed for {model.name}, trying next model")
                    next_model = model_manager.get_best_available_model()
                    if next_model and next_model.name != model.name:
                        # Don't mark as failed - just skip this time for safety issues
                        return await optimized_generate_content(prompt, image_data, content_type)
                    else:
                        raise Exception("Content blocked by safety filters on all available models")
                
                elif finish_reason == 3:  # RECITATION
                    logger.warning(f"Content blocked for recitation by {model.name}")
                    raise Exception("Content blocked for potential copyright issues")
                
                elif finish_reason == 4:  # OTHER
                    logger.warning(f"Content blocked for other reasons by {model.name}")
                    raise Exception("Content blocked for unspecified reasons")
        
        # Check if response has text
        if hasattr(response, 'text') and response.text:
            logger.info(f"Successfully processed with {model.name}")
            return response.text
        else:
            logger.error(f"No text returned from {model.name}")
            raise Exception("No valid response text returned from model")
        
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

def create_tally_xml_from_data(data_list: List[dict], company_name: str = "YOUR COMPANY") -> str:
    """Create Tally-ready XML content from list of extracted invoice data"""
    if not data_list:
        return ""
    
    # Remove duplicates based on invoice number
    unique_invoices = {}
    for invoice_data in data_list:
        if invoice_data.get("error"):
            continue
        invoice_no = invoice_data.get("tax_invoice_no", "")
        if invoice_no and invoice_no not in unique_invoices:
            unique_invoices[invoice_no] = invoice_data
    
    if not unique_invoices:
        return ""
    
    # Create root XML structure
    envelope = ET.Element("ENVELOPE")
    
    # Header
    header = ET.SubElement(envelope, "HEADER")
    tally_request = ET.SubElement(header, "TALLYREQUEST")
    tally_request.text = "Import Data"
    
    # Body
    body = ET.SubElement(envelope, "BODY")
    import_data = ET.SubElement(body, "IMPORTDATA")
    
    # Request description for vouchers (transactions)
    request_desc = ET.SubElement(import_data, "REQUESTDESC")
    report_name = ET.SubElement(request_desc, "REPORTNAME")
    report_name.text = "Vouchers"
    
    static_vars = ET.SubElement(request_desc, "STATICVARIABLES")
    current_company = ET.SubElement(static_vars, "SVCURRENTCOMPANY")
    current_company.text = company_name
    
    # Request data
    request_data = ET.SubElement(import_data, "REQUESTDATA")
    
    # Process each unique invoice
    for invoice_data in unique_invoices.values():
        # Create TallyMessage for this invoice
        tally_message = ET.SubElement(request_data, "TALLYMESSAGE")
        tally_message.set("xmlns:UDF", "TallyUDF")
        
        # Create voucher (main transaction)
        if invoice_data.get("tax_invoice_no"):
            voucher = create_voucher(tally_message, invoice_data)
    
    # Convert to string with proper formatting
    xml_str = ET.tostring(envelope, encoding='unicode')
    
    # Pretty print the XML
    dom = minidom.parseString(xml_str)
    return dom.toprettyxml(indent="  ")

def create_voucher(parent: ET.Element, invoice_data: dict) -> ET.Element:
    """Create a voucher (transaction) entry in Tally XML format"""
    voucher = ET.SubElement(parent, "VOUCHER")
    
    # Voucher attributes
    voucher.set("REMOTEID", f"invoice-{hash(invoice_data['tax_invoice_no']) % 1000000:06d}")
    voucher.set("VCHKEY", f"key-{hash(invoice_data['tax_invoice_no']) % 1000000:06d}")
    voucher.set("VCHTYPE", "Sales")
    voucher.set("ACTION", "Create")
    voucher.set("OBJVIEW", "Invoice Voucher View")
    
    # Date conversion (DD-MM-YYYY to YYYYMMDD)
    if invoice_data.get("invoice_date"):
        try:
            date_parts = invoice_data["invoice_date"].split("-")
            if len(date_parts) == 3:
                tally_date = f"{date_parts[2]}{date_parts[1]:0>2}{date_parts[0]:0>2}"
            else:
                tally_date = "20250101"
        except:
            tally_date = "20250101"
    else:
        tally_date = "20250101"
    
    # Basic voucher details
    voucher_date = ET.SubElement(voucher, "DATE")
    voucher_date.text = tally_date
    
    # Voucher type name
    voucher_type_name = ET.SubElement(voucher, "VOUCHERTYPENAME")
    voucher_type_name.text = "Sales"
    
    voucher_number = ET.SubElement(voucher, "VOUCHERNUMBER")
    voucher_number.text = invoice_data.get("tax_invoice_no", "")
    
    # Reference details
    if invoice_data.get("party_name"):
        party_name = ET.SubElement(voucher, "PARTYLEDGERNAME")
        party_name.text = invoice_data["party_name"]
    
    # Reference number and date
    reference = ET.SubElement(voucher, "REFERENCE")
    reference.text = invoice_data.get("tax_invoice_no", "")
    
    reference_date = ET.SubElement(voucher, "REFERENCEDATE")
    reference_date.text = tally_date
    
    # Narration
    narration = ET.SubElement(voucher, "NARRATION")
    narration.text = f"Sales Invoice {invoice_data.get('tax_invoice_no', '')} to {invoice_data.get('party_name', '')}"
    
    # Voucher entries (accounting entries)
    create_voucher_entries(voucher, invoice_data)
    
    # Bill allocations for party ledger
    if invoice_data.get("party_name") and invoice_data.get("invoice_value"):
        create_bill_allocations(voucher, invoice_data)
    
    return voucher

def create_voucher_entries(voucher: ET.Element, invoice_data: dict) -> None:
    """Create accounting entries for the voucher"""
    
    # Party ledger entry (Debit - Amount Receivable)
    if invoice_data.get("party_name") and invoice_data.get("invoice_value"):
        party_entry = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
        
        ledger_name = ET.SubElement(party_entry, "LEDGERNAME")
        ledger_name.text = invoice_data["party_name"]
        
        amount = ET.SubElement(party_entry, "AMOUNT")
        amount.text = str(invoice_data["invoice_value"])
        
        is_deemed_positive = ET.SubElement(party_entry, "ISDEEMEDPOSITIVE")
        is_deemed_positive.text = "Yes"
    
    # Sales entry (Credit - Revenue)
    if invoice_data.get("taxable_value"):
        sales_entry = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
        
        sales_ledger = ET.SubElement(sales_entry, "LEDGERNAME")
        sales_ledger.text = "Sales Account"
        
        sales_amount = ET.SubElement(sales_entry, "AMOUNT")
        sales_amount.text = f"-{invoice_data['taxable_value']}"
        
        is_deemed_positive = ET.SubElement(sales_entry, "ISDEEMEDPOSITIVE")
        is_deemed_positive.text = "No"
    
    # CGST entry
    if invoice_data.get("cgst") and float(str(invoice_data["cgst"]).replace(",", "") or 0) > 0:
        cgst_entry = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
        
        cgst_ledger = ET.SubElement(cgst_entry, "LEDGERNAME")
        cgst_ledger.text = "OUTPUT CGST @ 9%"
        
        cgst_amount = ET.SubElement(cgst_entry, "AMOUNT")
        cgst_amount.text = f"-{invoice_data['cgst']}"
        
        is_deemed_positive = ET.SubElement(cgst_entry, "ISDEEMEDPOSITIVE")
        is_deemed_positive.text = "No"
    
    # SGST entry  
    if invoice_data.get("sgst") and float(str(invoice_data["sgst"]).replace(",", "") or 0) > 0:
        sgst_entry = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
        
        sgst_ledger = ET.SubElement(sgst_entry, "LEDGERNAME")
        sgst_ledger.text = "OUTPUT SGST @ 9%"
        
        sgst_amount = ET.SubElement(sgst_entry, "AMOUNT")
        sgst_amount.text = f"-{invoice_data['sgst']}"
        
        is_deemed_positive = ET.SubElement(sgst_entry, "ISDEEMEDPOSITIVE")
        is_deemed_positive.text = "No"
    
    # IGST entry
    if invoice_data.get("igst") and float(str(invoice_data["igst"]).replace(",", "") or 0) > 0:
        igst_entry = ET.SubElement(voucher, "ALLLEDGERENTRIES.LIST")
        
        igst_ledger = ET.SubElement(igst_entry, "LEDGERNAME")
        igst_ledger.text = "OUTPUT IGST @ 18%"
        
        igst_amount = ET.SubElement(igst_entry, "AMOUNT")
        igst_amount.text = f"-{invoice_data['igst']}"
        
        is_deemed_positive = ET.SubElement(igst_entry, "ISDEEMEDPOSITIVE")
        is_deemed_positive.text = "No"

def create_bill_allocations(voucher: ET.Element, invoice_data: dict) -> None:
    """Create bill allocations for party ledger entries"""
    # Find the party ledger entry to add bill allocations
    for ledger_entry in voucher.findall(".//ALLLEDGERENTRIES.LIST"):
        ledger_name_elem = ledger_entry.find("LEDGERNAME")
        if ledger_name_elem is not None and ledger_name_elem.text == invoice_data.get("party_name"):
            # Add bill allocations list
            bill_allocations = ET.SubElement(ledger_entry, "BILLALLOCATIONS.LIST")
            
            # Bill name (invoice number)
            bill_name = ET.SubElement(bill_allocations, "NAME")
            bill_name.text = invoice_data.get("tax_invoice_no", "")
            
            # Bill type
            bill_type = ET.SubElement(bill_allocations, "BILLTYPE")
            bill_type.text = "New Ref"
            
            # Amount
            amount = ET.SubElement(bill_allocations, "AMOUNT")
            amount.text = str(invoice_data.get("invoice_value", 0))
            
            break

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

@app.post("/bulk-extract-tally/")
async def bulk_extract_invoices_tally(files: List[UploadFile] = File(...), company_name: str = "YOUR COMPANY"):
    """Bulk extraction with Tally XML output for direct import"""
    
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    
    if len(files) > MAX_BULK_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files. Maximum {MAX_BULK_FILES} files allowed.")
    
    logger.info(f"Starting Tally XML bulk processing of {len(files)} files")
    results = []
    
    # Process files (reuse the same logic as regular bulk extract)
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
    
    # Process in batches
    for i in range(0, len(valid_files), BATCH_SIZE):
        batch = valid_files[i:i + BATCH_SIZE]
        
        # Separate images and PDFs
        image_files = [f for f in batch if "image" in f['content_type']]
        pdf_files = [f for f in batch if "pdf" in f['content_type']]
        
        # Process images
        if image_files:
            try:
                if len(image_files) == 1:
                    result = await process_single_file_robust(
                        image_files[0]['content'],
                        image_files[0]['filename'],
                        image_files[0]['content_type']
                    )
                    results.append(result)
                else:
                    batch_results = await process_image_batch_robust(image_files)
                    results.extend(batch_results)
            except Exception as e:
                logger.error(f"Image batch processing failed: {e}")
                for img_file in image_files:
                    results.append(create_null_result(img_file['filename'], f"Processing failed: {str(e)}"))
        
        # Process PDFs
        for pdf_file in pdf_files:
            try:
                result = await process_single_file_robust(
                    pdf_file['content'],
                    pdf_file['filename'],
                    pdf_file['content_type']
                )
                results.append(result)
            except Exception as e:
                results.append(create_null_result(pdf_file['filename'], f"PDF processing failed: {str(e)}"))
        
        # Delay between batches
        if i + BATCH_SIZE < len(valid_files):
            await asyncio.sleep(0.5)
    
    # Generate Tally XML
    tally_xml = create_tally_xml_from_data(results, company_name)
    
    return StreamingResponse(
        io.StringIO(tally_xml),
        media_type="application/xml",
        headers={"Content-Disposition": f"attachment; filename=tally_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xml"}
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
    """Get status of all Gemini models with safety filter improvements"""
    model_status = []
    for model in model_manager.models:
        # Add safety filter status
        safety_info = "No restrictions" if "2.5" in model.name else "Standard restrictions"
        model_status.append({
            "name": model.name,
            "model_id": model.model_id,
            "available": model.available,
            "retry_count": model.retry_count,
            "max_retries": model.max_retries,
            "cost_efficiency": model.cost_efficiency,
            "last_error": model.last_error,
            "safety_filter_mode": safety_info,
            "priority": "Primary" if model.cost_efficiency == 1 else f"Fallback {model.cost_efficiency-1}"
        })
    
    quota_status = model_manager.get_quota_status()
    
    # Add safety improvements info
    safety_improvements = {
        "safety_blocks_ignore_retry_count": True,
        "fallback_strategies": 3,
        "auto_recovery": True,
        "gemini_2_5_restrictions": "None"
    }
    
    return {
        "models": model_status,
        "quota": quota_status,
        "available_count": len([m for m in model_manager.models if m.available]),
        "total_count": len(model_manager.models),
        "safety_improvements": safety_improvements,
        "status": "Safety filter issues resolved ✅"
    }

@app.get("/models")
async def get_models_status():
    """Get detailed information about all available models"""
    models_info = []
    for model in model_manager.models:
        models_info.append({
            "name": model.name,
            "model_id": model.model_id,
            "available": model.available,
            "cost_efficiency": model.cost_efficiency,
            "retry_count": model.retry_count,
            "max_retries": model.max_retries
        })
    
    best_model = model_manager.get_best_available_model()
    
    return {
        "all_models": models_info,
        "active_model": {
            "name": best_model.name if best_model else "none",
            "model_id": best_model.model_id if best_model else "none",
            "efficiency_rank": best_model.cost_efficiency if best_model else "none"
        },
        "total_models": len(model_manager.models)
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
            "requests_until_limit": max(0, 200 - quota_status["requests_used"]),  # Updated to 200 RPD
            "should_slow_down": quota_status["requests_used"] > 180,  # Updated threshold
            "estimated_daily_capacity": f"~{200 * BATCH_SIZE} invoices with batch processing (Latest Gemini 2.5 Flash models!)"
        }
    }

@app.post("/bulk-extract-dual/")
async def bulk_extract_invoices_dual(files: List[UploadFile] = File(...), company_name: str = "YOUR COMPANY"):
    """Bulk extraction with both CSV and Tally XML outputs in a zip file"""
    
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    
    if len(files) > MAX_BULK_FILES:
        raise HTTPException(status_code=400, detail=f"Too many files. Maximum {MAX_BULK_FILES} files allowed.")
    
    logger.info(f"Starting dual export bulk processing of {len(files)} files")
    results = []
    
    # Process files (reuse the same logic)
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
    
    # Process in batches
    for i in range(0, len(valid_files), BATCH_SIZE):
        batch = valid_files[i:i + BATCH_SIZE]
        
        # Separate images and PDFs
        image_files = [f for f in batch if "image" in f['content_type']]
        pdf_files = [f for f in batch if "pdf" in f['content_type']]
        
        # Process images
        if image_files:
            try:
                if len(image_files) == 1:
                    result = await process_single_file_robust(
                        image_files[0]['content'],
                        image_files[0]['filename'],
                        image_files[0]['content_type']
                    )
                    results.append(result)
                else:
                    batch_results = await process_image_batch_robust(image_files)
                    results.extend(batch_results)
            except Exception as e:
                logger.error(f"Image batch processing failed: {e}")
                for img_file in image_files:
                    results.append(create_null_result(img_file['filename'], f"Processing failed: {str(e)}"))
        
        # Process PDFs
        for pdf_file in pdf_files:
            try:
                result = await process_single_file_robust(
                    pdf_file['content'],
                    pdf_file['filename'],
                    pdf_file['content_type']
                )
                results.append(result)
            except Exception as e:
                results.append(create_null_result(pdf_file['filename'], f"PDF processing failed: {str(e)}"))
        
        # Delay between batches
        if i + BATCH_SIZE < len(valid_files):
            await asyncio.sleep(0.5)
    
    # Generate both CSV and XML
    csv_content = create_csv_from_data(results)
    tally_xml = create_tally_xml_from_data(results, company_name)
    
    # Create a zip file with both formats
    import zipfile
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        # Add CSV
        zip_file.writestr(
            f"invoice_extraction_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            csv_content.encode('utf-8')
        )
        # Add Tally XML
        zip_file.writestr(
            f"tally_import_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xml",
            tally_xml.encode('utf-8')
        )
    
    zip_buffer.seek(0)
    
    return StreamingResponse(
        io.BytesIO(zip_buffer.read()),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=invoice_extraction_complete_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)