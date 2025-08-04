#!/usr/bin/env python3
"""
Test script for the production invoice extractor API
"""
import asyncio
import aiohttp
import json
import os
from pathlib import Path

API_BASE_URL = "http://localhost:8000"

async def test_health_check():
    """Test the health check endpoint"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE_URL}/health") as response:
            data = await response.json()
            print(f"✅ Health Check: {data}")
            return response.status == 200

async def test_provider_status():
    """Test the model status endpoint"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE_URL}/models") as response:
            data = await response.json()
            available_models = data['available_count']
            total_models = data['total_count']
            print(f"📊 Models: {available_models}/{total_models} available")
            
            for model in data['models']:
                status = "✅" if model['available'] else "❌"
                print(f"  {status} {model['name']} - Retries: {model['retry_count']}/{model['max_retries']}")
            
            return available_models > 0

async def test_file_extraction():
    """Test file extraction with a proper sample file"""
    # Use the proper test image we created
    try:
        with open('test_invoice_proper.png', 'rb') as f:
            test_image_data = f.read()
    except FileNotFoundError:
        print("❌ Test image not found, creating a simple one...")
        # Create a minimal but valid PNG
        from PIL import Image
        import io
        img = Image.new('RGB', (100, 100), color='white')
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        test_image_data = buffer.getvalue()
    
    data = aiohttp.FormData()
    data.add_field('file', test_image_data, filename='test.png', content_type='image/png')
    
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_BASE_URL}/extract-details/", data=data) as response:
            if response.status == 200:
                result = await response.json()
                print(f"✅ File extraction successful: {result['success']}")
                print(f"📊 Extracted data fields: {len(result.get('data', {}))}")
                return True
            else:
                error = await response.text()
                print(f"❌ File extraction failed: {error}")
                return False

async def test_reset_providers():
    """Test resetting model retry counts"""
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_BASE_URL}/reset-models") as response:
            data = await response.json()
            print(f"🔄 Reset models: {data['message']}")
            return response.status == 200

async def main():
    """Run all tests"""
    print("🧪 Testing Production Invoice Extractor API\n")
    
    tests = [
        ("Health Check", test_health_check),
        ("Model Status", test_provider_status),
        ("File Extraction", test_file_extraction),
        ("Reset Models", test_reset_providers),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"Running {test_name}...")
        try:
            result = await test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ {test_name} failed with error: {e}")
            results.append((test_name, False))
        print()
    
    # Summary
    print("📋 Test Results:")
    passed = 0
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status} {test_name}")
        if result:
            passed += 1
    
    print(f"\n🎯 Overall: {passed}/{len(results)} tests passed")
    
    if passed == len(results):
        print("🎉 All tests passed! Your API is ready for production.")
    else:
        print("⚠️  Some tests failed. Check your configuration.")

if __name__ == "__main__":
    asyncio.run(main())
