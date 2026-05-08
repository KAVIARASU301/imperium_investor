#!/bin/bash
echo "Cleaning previous build..."
rm -rf build dist

echo "Building trading terminal..."
pyinstaller main.spec --clean --noconfirm

echo "Setting permissions..."
chmod +x dist/qullamaggie/qullamaggie

echo "Build complete. Size:"
du -sh dist/qullamaggie/

echo "Test run:"
cd dist/qullamaggie && ./qullamaggie