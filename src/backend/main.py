import google.generativeai as genai
import io
import os
from PIL import Image
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# --- Main Configuration ---
load_dotenv()
app = FastAPI()
# Load API key from environment variable
api_key = os.getenv("GOOGLE_API_KEY")
if not api_key:
    raise ValueError("GOOGLE_API_KEY environment variable is not set")
genai.configure(api_key=api_key)
origins = ["http://localhost:5173", "http://localhost:5174"]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.post("/extract-details/")
async def extract_invoice_details(file: UploadFile = File(...)):
    print("--- Function Entered: All local processing confirmed OK. ---")
    
    prompt_parts = [
        "Extract the PARTY NAME, PARTY GSTIN, TAX INVOICE NO., INVOICE DATE, TAXABLE VALUE, CGST, SGST, IGST, INVOICE VALUE from this document. Provide the output in a clean JSON format."
    ]
    
    try:
        contents = await file.read()

        # Prepare the file part for the prompt
        if "image" in file.content_type:
            prompt_parts.append(Image.open(io.BytesIO(contents)))
        elif "pdf" in file.content_type:
            prompt_parts.append({"mime_type": file.content_type, "data": contents})
        else:
            raise HTTPException(status_code=400, detail="Unsupported file type.")

        # --- FINAL TEST: Calling the Gemini API ---
        print("--- Attempting to call the Gemini API... ---")
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt_parts)
        print("--- Gemini API call successful! ---")
        
        return {"extracted_data": response.text}

    except Exception as e:
        # If it fails, this will now hopefully catch and print the error
        print(f"!!! CRITICAL ERROR during Gemini API call: {repr(e)} !!!")
        raise HTTPException(status_code=500, detail=f"An error occurred with the AI model: {repr(e)}")