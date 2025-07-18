import google.generativeai as genai
import io
import os
from PIL import Image
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
import json
import csv
from typing import List
import zipfile
import tempfile

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="Invoice Extractor API")

# Configure Gemini API
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("GOOGLE_API_KEY environment variable is not set")

genai.configure(api_key=api_key)
model = genai.GenerativeModel('gemini-1.5-flash')

# CORS configuration
origins = ["http://localhost:5173", "http://localhost:5174", "http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

# Extraction prompt
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

# File size limit
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

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
    """Extract invoice details"""
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
        
        # Prepare content for AI
        if "image" in file.content_type:
            image = Image.open(io.BytesIO(contents))
            image = optimize_image(image)
            prompt_parts = [EXTRACTION_PROMPT, image]
        elif "pdf" in file.content_type:
            prompt_parts = [EXTRACTION_PROMPT, {"mime_type": file.content_type, "data": contents}]
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")
        
        # Generate content
        response = model.generate_content(prompt_parts)
        
        # Parse response
        extracted_data = parse_response(response.text)
        
        return {"success": True, "data": extracted_data}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")

def process_single_file(file_content: bytes, filename: str, content_type: str) -> dict:
    """Process a single file and return extracted data with filename"""
    try:
        # Prepare content for AI
        if "image" in content_type:
            image = Image.open(io.BytesIO(file_content))
            image = optimize_image(image)
            prompt_parts = [EXTRACTION_PROMPT, image]
        elif "pdf" in content_type:
            prompt_parts = [EXTRACTION_PROMPT, {"mime_type": content_type, "data": file_content}]
        else:
            return {"filename": filename, "error": "Unsupported file type"}
        
        # Generate content
        response = model.generate_content(prompt_parts)
        
        # Parse response
        extracted_data = parse_response(response.text)
        
        # Add filename to the data
        extracted_data["filename"] = filename
        
        return extracted_data
        
    except Exception as e:
        return {"filename": filename, "error": str(e)}

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

@app.post("/bulk-extract/")
async def bulk_extract_invoices(files: List[UploadFile] = File(...)):
    """Extract details from multiple invoice files and return CSV"""
    
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    
    if len(files) > 20:  # Limit to 20 files
        raise HTTPException(status_code=400, detail="Too many files. Maximum 20 files allowed.")
    
    results = []
    
    # Process each file
    for file in files:
        try:
            # Validate file
            validate_file(file)
            
            # Check file size
            contents = await file.read()
            if len(contents) > MAX_FILE_SIZE:
                results.append({
                    "filename": file.filename,
                    "error": f"File too large. Maximum size: {MAX_FILE_SIZE / (1024*1024):.1f}MB"
                })
                continue
            
            # Process the file
            result = process_single_file(contents, file.filename, file.content_type)
            results.append(result)
            
        except Exception as e:
            results.append({
                "filename": file.filename or "unknown",
                "error": str(e)
            })
    
    # Create CSV content
    csv_content = create_csv_from_data(results)
    
    # Return CSV as downloadable file
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invoice_extraction_results.csv"}
    )