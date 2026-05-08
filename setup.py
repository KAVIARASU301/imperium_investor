# setup.py
"""
Setup script for Imperium Swing Trader
"""

from setuptools import setup, find_packages

# A more robust way to handle the README file
try:
    with open("README.md", "r", encoding="utf-8") as fh:
        long_description = fh.read()
except FileNotFoundError:
    long_description = "Imperium Swing Trader desktop application for broker-assisted swing trading"

setup(
    name="Imperium Swing Trader",
    version="1.0.0",
    author="Kaviarasu",
    author_email="kaviarasu301@gmail.com",
    url="https://github.com/kaviarasu301/",
    description="Imperium Swing Trader desktop application for broker-assisted swing trading",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
    install_requires=[
        "PySide6>=6.5.0",
        "kiteconnect>=5.0.0",
        "cryptography>=41.0.0",
        "python-dateutil>=2.8.2",
        "rapidfuzz>=3.14.1",
    ],
    entry_points={
        "console_scripts": [
            "imperium=main:main",
        ],
    },
    zip_safe=False, # Recommended for PySide6/GUI applications
)