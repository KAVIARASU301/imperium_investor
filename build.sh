#!/bin/bash
echo "Cleaning previous build..."
rm -rf build dist

echo "Building trading terminal..."
pyinstaller main.spec --clean --noconfirm

echo "Setting permissions..."
chmod +x dist/imperium/imperium

echo "Build complete. Size:"
du -sh dist/imperium/

echo "Test run:"
cd dist/imperium && ./imperium