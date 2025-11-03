#!/usr/bin/env python3
"""
Test Script for Invoice Extractor API
=====================================

Tests API endpoints, environment variables, and basic job creation.
XML and CSV generation should be tested manually through the web interface.

Usage:
    python test_server.py
"""

import requests
import time
import os
import redis
import google.genai as genai
from google.genai import types
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
API_BASE_URL = "http://localhost:8000"
TEST_COMPANY = "Test Company Ltd"

# Redis Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_DB = int(os.getenv("REDIS_DB", 0))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)

# Google API Configuration
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

def test_environment_variables():
    """Test if all required environment variables are set"""
    required_vars = {
        "GOOGLE_API_KEY": "Google Gemini API key",
    }
    
    optional_vars = {
        "REDIS_HOST": "Redis host (defaults to localhost)",
        "REDIS_PORT": "Redis port (defaults to 6379)",
        "REDIS_DB": "Redis database (defaults to 0)",
        "REDIS_PASSWORD": "Redis password (optional)"
    }
    
    missing_vars = []
    
    # Check required variables
    for var, description in required_vars.items():
        value = os.getenv(var)
        if not value:
            missing_vars.append(f"{var}: {description}")
    
    # Check optional variables and show warnings
    for var, description in optional_vars.items():
        value = os.getenv(var)
        if not value and var in ["REDIS_HOST", "REDIS_PORT"]:
            print(f"⚠️  {var} not set, using default")
    
    if missing_vars:
        print("❌ Environment Variables: FAILED")
        for var in missing_vars:
            print(f"   Missing: {var}")
        return False
    else:
        print("✅ Environment Variables: PASSED")
        return True

def test_redis_connection():
    """Test Redis connection and basic operations"""
    try:
        # Create Redis connection
        if REDIS_PASSWORD:
            r = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=REDIS_PASSWORD,
                decode_responses=True
            )
        else:
            r = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                decode_responses=True
            )
        
        # Test basic operations
        test_key = f"test:{int(time.time())}"
        test_value = "redis_test_value"
        
        # Set and get test
        r.set(test_key, test_value, ex=60)  # Expire in 60 seconds
        retrieved_value = r.get(test_key)
        
        if retrieved_value == test_value:
            # Clean up
            r.delete(test_key)
            print("✅ Redis Connection: PASSED")
            return True
        else:
            print(f"❌ Redis Connection: FAILED (Value mismatch)")
            return False
            
    except redis.ConnectionError:
        print("❌ Redis Connection: FAILED (Connection Error)")
        print(f"   Check if Redis is running on {REDIS_HOST}:{REDIS_PORT}")
        return False
    except Exception as e:
        print(f"❌ Redis Connection: FAILED ({e})")
        return False

def test_gemini_api():
    """Test Gemini API connection and basic functionality"""
    try:
        if not GOOGLE_API_KEY:
            print("❌ Gemini API: FAILED (No API key found)")
            print("   Set GOOGLE_API_KEY environment variable")
            return False
        
        # Configure Gemini with new client-based API
        client = genai.Client(api_key=GOOGLE_API_KEY)
        
        # Test with a simple prompt using a commonly available model
        try:
            model_id = 'gemini-2.0-flash-exp'
        except:
            try:
                model_id = 'gemini-1.5-flash'
            except:
                model_id = 'gemini-pro'
        
        test_prompt = "Extract the number 42 from this text and return only a JSON object with the field 'number': The answer is 42."
        
        response = client.models.generate_content(
            model=model_id,
            contents=test_prompt,
            config=genai.types.GenerateContentConfig(
                temperature=0,
                max_output_tokens=100
            )
        )
        
        if response.text and ("42" in response.text or "number" in response.text.lower()):
            print("✅ Gemini API: PASSED")
            print(f"   Model response: {response.text[:50]}...")
            return True
        else:
            print("❌ Gemini API: FAILED (Unexpected response)")
            print(f"   Response: {response.text[:100]}...")
            return False
            
    except Exception as e:
        print(f"❌ Gemini API: FAILED ({e})")
        if "API_KEY" in str(e) or "403" in str(e):
            print("   Check if your Google API key is valid and has Gemini access")
        elif "404" in str(e):
            print("   The requested model may not be available")
        return False

def test_health_check():
    """Test if the API is running"""
    try:
        response = requests.get(f"{API_BASE_URL}/health")
        if response.status_code == 200:
            data = response.json()
            print("✅ API Health Check: PASSED")
            print(f"   Status: {data.get('status')}")
            print(f"   Version: {data.get('version')}")
            return True
        else:
            print(f"❌ API Health Check: FAILED ({response.status_code})")
            return False
    except requests.exceptions.ConnectionError:
        print("❌ API Health Check: FAILED (Connection Error - Is the server running?)")
        return False
    except Exception as e:
        print(f"❌ API Health Check: FAILED ({e})")
        return False

def test_api_endpoints():
    """Test additional API endpoints"""
    try:
        # Test stats endpoint
        response = requests.get(f"{API_BASE_URL}/stats")
        if response.status_code == 200:
            print("✅ Stats Endpoint: PASSED")
            return True
        else:
            print(f"❌ Stats Endpoint: FAILED ({response.status_code})")
            return False
        
    except Exception as e:
        print(f"❌ API Endpoints: FAILED ({e})")
        return False

def test_job_creation():
    """Test basic job creation with a simple file"""
    try:
        # Create a simple test image
        from PIL import Image
        import io
        
        img = Image.new('RGB', (400, 600), color='white')
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_buffer.seek(0)
        
        # Prepare form data
        files = {'files': ('test_invoice.png', img_buffer, 'image/png')}
        data = {
            'company_name': TEST_COMPANY,
            'transaction_type': 'Sales - GST 18%',
            'bypass_cache': 'false'
        }
        
        # Create job
        response = requests.post(f"{API_BASE_URL}/jobs/create", files=files, data=data)
        
        if response.status_code == 200:
            result = response.json()
            job_id = result.get('job_id')
            print(f"✅ Job Creation: PASSED (Job ID: {job_id[:8]}...)")
            print(f"   Total files: {result.get('total_files')}")
            print(f"   Transaction type: {data['transaction_type']}")
            
            # Wait a moment and check job status
            time.sleep(3)
            status_response = requests.get(f"{API_BASE_URL}/jobs/status/{job_id}")
            if status_response.status_code == 200:
                status = status_response.json()
                print(f"✅ Job Status Check: PASSED")
                print(f"   Status: {status.get('status')}")
                print(f"   Progress: {status.get('progress_percentage', 0):.1f}%")
                print(f"   Processed: {status.get('processed_files')}/{status.get('total_files')}")
                return True
            else:
                print(f"❌ Job Status Check: FAILED ({status_response.status_code})")
                return False
        else:
            print(f"❌ Job Creation: FAILED ({response.status_code})")
            if response.content:
                print(f"   Error: {response.json().get('detail', 'Unknown error')}")
            return False
            
    except Exception as e:
        print(f"❌ Job Creation: FAILED ({e})")
        return False

def main():
    """Run focused API and infrastructure tests"""
    print("🧪 Invoice Extractor API Tests")
    print("=" * 50)
    
    # Check if we have required dependencies
    try:
        from PIL import Image
        import redis
        import google.genai as genai
        from google.genai import types
    except ImportError as e:
        print(f"❌ Missing required dependency: {e}")
        print("   Install with: pip install -r requirements.txt")
        return
    
    # Define test categories
    infrastructure_tests = [
        ("Environment Variables", test_environment_variables),
        ("Redis Connection", test_redis_connection),
        ("Gemini API", test_gemini_api),
    ]
    
    api_tests = [
        ("API Health Check", test_health_check),
        ("API Endpoints", test_api_endpoints),
        ("Job Creation", test_job_creation),
    ]
    
    all_test_categories = [
        ("🏗️  Infrastructure Tests", infrastructure_tests),
        ("🌐 API Tests", api_tests),
    ]
    
    total_passed = 0
    total_tests = sum(len(tests) for _, tests in all_test_categories)
    
    for category_name, tests in all_test_categories:
        print(f"\n{category_name}")
        print("-" * 40)
        
        category_passed = 0
        for test_name, test_func in tests:
            print(f"Running {test_name}...")
            if test_func():
                category_passed += 1
                total_passed += 1
            print()
        
        print(f"📊 {category_name} Results: {category_passed}/{len(tests)} passed")
    
    print("=" * 50)
    print(f"🎯 Overall Results: {total_passed}/{total_tests} tests passed")
    print(f"📈 Success Rate: {(total_passed/total_tests)*100:.1f}%")
    
    if total_passed == total_tests:
        print("🎉 All tests passed! Your API is fully functional.")
    elif total_passed >= total_tests * 0.8:
        print("✅ Most tests passed! Minor issues may exist.")
    elif total_passed >= total_tests * 0.5:
        print("⚠️  Some tests failed. Check your configuration.")
    else:
        print("❌ Many tests failed. Please check your setup:")
        print("   1. Ensure all dependencies are installed: pip install -r requirements.txt")
        print("   2. Start the backend server: python src/main.py")
        print("   3. Verify Redis is running and accessible")
        print("   4. Check your Google Gemini API key is valid")
        print("   5. Review environment variables in .env file")

if __name__ == "__main__":
    main()
