#!/usr/bin/env python3
"""
Test PDF processing specifically - Simple version without reportlab
"""
import asyncio
import aiohttp
import base64

async def test_pdf_processing():
    """Test PDF file processing with a minimal PDF"""
    
    # Create a minimal PDF manually (just enough to test the endpoint)
    # This is a very basic PDF structure
    minimal_pdf = b"""%PDF-1.4
1 0 obj
<<
/Type /Catalog
/Pages 2 0 R
>>
endobj

2 0 obj
<<
/Type /Pages
/Kids [3 0 R]
/Count 1
>>
endobj

3 0 obj
<<
/Type /Page
/Parent 2 0 R
/MediaBox [0 0 612 792]
/Contents 4 0 R
>>
endobj

4 0 obj
<<
/Length 44
>>
stream
BT
/F1 12 Tf
72 720 Td
(Invoice Test) Tj
ET
endstream
endobj

xref
0 5
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000207 00000 n 
trailer
<<
/Size 5
/Root 1 0 R
>>
startxref
296
%%EOF"""
    
    print(f"📄 Created minimal test PDF with {len(minimal_pdf)} bytes")
    
    # Test the API
    data = aiohttp.FormData()
    data.add_field('file', minimal_pdf, filename='test_invoice.pdf', content_type='application/pdf')
    
    async with aiohttp.ClientSession() as session:
        async with session.post("http://localhost:8000/extract-details/", data=data) as response:
            if response.status == 200:
                result = await response.json()
                print(f"✅ PDF extraction successful: {result['success']}")
                print(f"📊 Extracted data: {result.get('data', {})}")
                return True
            else:
                error = await response.text()
                print(f"❌ PDF extraction failed: {error}")
                return False

if __name__ == "__main__":
    asyncio.run(test_pdf_processing())
