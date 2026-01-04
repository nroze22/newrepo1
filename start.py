#!/usr/bin/env python3
"""
Quick start script for the AI Consultant Platform.
Handles setup, validation, and launch.
"""

import os
import sys
import subprocess
from pathlib import Path


def check_python_version():
    """Ensure Python 3.8+ is being used."""
    if sys.version_info < (3, 8):
        print("❌ Python 3.8 or higher is required")
        sys.exit(1)
    print(f"✅ Python {sys.version_info.major}.{sys.version_info.minor} detected")


def check_env_file():
    """Check if .env file exists and has required variables."""
    env_file = Path(".env")

    if not env_file.exists():
        print("\n⚠️  .env file not found. Creating from .env.example...")
        example = Path(".env.example")
        if example.exists():
            with open(example) as src, open(env_file, 'w') as dst:
                dst.write(src.read())
            print("✅ .env file created")
        else:
            print("❌ .env.example not found")
            return False

    # Check for required variables
    with open(env_file) as f:
        env_content = f.read()

    required_vars = ["GEMINI_API_KEY"]
    missing_vars = []

    for var in required_vars:
        if f"{var}=" not in env_content or f"{var}=your_" in env_content:
            missing_vars.append(var)

    if missing_vars:
        print("\n⚠️  Missing or placeholder values for required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\n📝 Please edit .env file and add your API keys:")
        print("   1. Get Gemini API key: https://makersuite.google.com/app/apikey")
        print("   2. Add it to .env file: GEMINI_API_KEY=your_actual_key_here")
        return False

    print("✅ Environment variables configured")
    return True


def install_dependencies():
    """Install required Python packages."""
    print("\n📦 Installing dependencies...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "requirements.txt", "-q"])
        print("✅ Dependencies installed")
        return True
    except subprocess.CalledProcessError:
        print("❌ Failed to install dependencies")
        return False


def start_server():
    """Start the FastAPI server."""
    print("\n🚀 Starting AI Consultant Platform...")
    print("=" * 60)
    print("📱 Access the application at: http://localhost:8000")
    print("📚 API documentation at: http://localhost:8000/docs")
    print("=" * 60)
    print("\n💡 Tips:")
    print("   - Allow camera/microphone access when prompted")
    print("   - Choose a consultant mode before starting")
    print("   - The AI will analyze your video/screen in real-time")
    print("\n🛑 Press Ctrl+C to stop the server\n")

    try:
        os.chdir("backend")
        subprocess.run([sys.executable, "main.py"])
    except KeyboardInterrupt:
        print("\n\n👋 Server stopped. Thank you for using AI Consultant Platform!")
    except Exception as e:
        print(f"\n❌ Error starting server: {e}")


def main():
    print("=" * 60)
    print("🎯 AI CONSULTANT PLATFORM - QUICK START")
    print("=" * 60)

    # Step 1: Check Python version
    check_python_version()

    # Step 2: Check environment file
    if not check_env_file():
        print("\n⚠️  Setup incomplete. Please configure .env file and run again.")
        sys.exit(1)

    # Step 3: Install dependencies
    if not install_dependencies():
        print("\n⚠️  Failed to install dependencies. Please check your internet connection.")
        sys.exit(1)

    # Step 4: Start server
    start_server()


if __name__ == "__main__":
    main()
