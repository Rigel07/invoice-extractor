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
    """Test the provider status endpoint"""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE_URL}/providers") as response:
            data = await response.json()
            print(f"📊 Providers: {data['available_count']}/{data['total_count']} available")
            
            for provider in data['providers']:
                status = "✅" if provider['available'] else "❌"
                print(f"  {status} {provider['name']} - Retries: {provider['retry_count']}/{provider['max_retries']}")
            
            return data['available_count'] > 0

async def test_file_extraction():
    """Test file extraction with a sample file"""
    # Create a simple test image (1x1 pixel PNG)
    test_image_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\x0eIDATx\x9cc\xf8\x0f\x00\x00\x01\x00\x01\x00\x00\x00\x00\x00\x00\x00\x00IEND\xaeB`\x82'
    
    data = aiohttp.FormData()
    data.add_field('file', test_image_data, filename='test.png', content_type='image/png')
    
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_BASE_URL}/extract-details/", data=data) as response:
            if response.status == 200:
                result = await response.json()
                print(f"✅ File extraction successful: {result['success']}")
                return True
            else:
                error = await response.text()
                print(f"❌ File extraction failed: {error}")
                return False

async def test_reset_providers():
    """Test resetting provider retry counts"""
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{API_BASE_URL}/reset-providers") as response:
            data = await response.json()
            print(f"🔄 Reset providers: {data['message']}")
            return response.status == 200

async def main():
    """Run all tests"""
    print("🧪 Testing Production Invoice Extractor API\n")
    
    tests = [
        ("Health Check", test_health_check),
        ("Provider Status", test_provider_status),
        ("File Extraction", test_file_extraction),
        ("Reset Providers", test_reset_providers),
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
