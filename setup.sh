#!/bin/bash

# Detect Python command (python3 or python)
if command -v python3 &>/dev/null; then
    PYTHON_CMD=python3
elif command -v python &>/dev/null; then
    PYTHON_CMD=python
else
    echo "Error: Python not found. Please install Python 3.x"
    exit 1
fi

if [ -d "venv" ]; then
    echo "Virtual environment exists."
else
    echo "Creating new virtual environment..."
    $PYTHON_CMD -m venv venv
    # Try to source the activate script
    ACTIVATE_SCRIPT="venv/bin/activate"
    if [ -f "$ACTIVATE_SCRIPT" ]; then
        source "$ACTIVATE_SCRIPT"
    else
        echo "Error: Could not find activation script at $ACTIVATE_SCRIPT"
        exit 1
    fi
    pip install -r requirements.txt
    echo "Virtual environment created and dependencies installed!"
fi

echo "Please activate it with: source venv/bin/activate"