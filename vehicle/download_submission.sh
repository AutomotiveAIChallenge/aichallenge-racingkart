#!/bin/bash

# Wrapper script for list_submissions.py
# This script provides a more user-friendly interface for listing and downloading submissions

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_SCRIPT="$SCRIPT_DIR/download_submission.py"

# Default values
OUTPUT_DIR="./downloads"
# API_BASE_URL="${API_BASE_URL:-http://localhost:8000}"
API_BASE_URL="https://aichallenge-board.jsae.or.jp"

# Function to print colored output
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "List recent submissions and download selected one"
    echo ""
    echo "Options:"
    echo "  -u, --username USERNAME    AWS Cognito username/email"
    echo "  -p, --password PASSWORD    AWS Cognito password"
    echo "  -o, --output DIR           Output directory (default: ./downloads/)"
    echo "  -s, --submission-id ID     Download specific submission by ID (skips listing)"
    echo "  -i, --user-id ID           User ID (only available for admin users)"
    echo "  -a, --api-url URL          API base URL (default: http://localhost:8000)"
    echo "  -v, --verbose              Enable verbose logging"
    echo "  -h, --help                 Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  API_BASE_URL               API base URL"
    echo "  USERNAME                   Default username (if not provided via -u)"
    echo "  PASSWORD                   Default password (if not provided via -p)"
    echo ""
    echo "Examples:"
    echo "  # List submissions and choose interactively"
    echo "  $0 -u user@example.com -p mypassword"
    echo ""
    echo "  # Download specific submission by ID"
    echo "  $0 -u user@example.com -p mypassword -s abc123"
    echo ""
    echo "  # With custom output directory"
    echo "  $0 -u user@example.com -p mypassword -o ./my_downloads/"
    echo ""
    echo "  # With custom API URL"
    echo "  API_BASE_URL=https://api.example.com $0 -u user@example.com -p mypassword"
    echo ""
    echo "Interactive mode (will prompt for credentials):"
    echo "  $0"
}

# Function to check if Python script exists
check_python_script() {
    if [ ! -f "$PYTHON_SCRIPT" ]; then
        print_error "Python script not found: $PYTHON_SCRIPT"
        exit 1
    fi
}

# Function to check if Python is available
check_python() {
    if ! command -v python3 &>/dev/null; then
        print_error "Python 3 is not installed or not in PATH"
        exit 1
    fi
}

# Function to check if requests module is available
check_requests() {
    if ! python3 -c "import requests" 2>/dev/null; then
        print_error "Python 'requests' module is not installed"
        print_info "Install it with: pip install requests"
        exit 1
    fi
}

# Function to get user input securely
get_password() {
    local prompt="$1"
    local password

    if [ -t 0 ]; then
        # Terminal input available
        read -r -s -p "$prompt" password
        echo >&2 # Add newline after hidden input (redirect to stderr so it doesn't interfere)
    else
        # No terminal input (e.g., piped input)
        read -r password
    fi

    echo "$password"
}

# Parse command line arguments
USERNAME=""
PASSWORD=""
SUBMISSION_ID=""
VERBOSE=""

while [[ $# -gt 0 ]]; do
    case $1 in
    -u | --username)
        USERNAME="$2"
        shift 2
        ;;
    -p | --password)
        PASSWORD="$2"
        shift 2
        ;;
    -o | --output)
        OUTPUT_DIR="$2"
        shift 2
        ;;
    -s | --submission-id)
        SUBMISSION_ID="$2"
        shift 2
        ;;
    -i | --user-id)
        USER_ID="$2"
        shift 2
        ;;
    -a | --api-url)
        API_BASE_URL="$2"
        shift 2
        ;;
    -v | --verbose)
        VERBOSE="--verbose"
        shift
        ;;
    -h | --help)
        show_usage
        exit 0
        ;;
    *)
        print_error "Unknown option: $1"
        show_usage
        exit 1
        ;;
    esac
done

# Main execution
main() {
    print_info "🚀 Starting submission listing script..."

    # Check prerequisites
    check_python
    check_python_script
    check_requests

    # Get credentials if not provided via command line
    if [ -z "$USERNAME" ]; then
        # Prompt user for input
        read -r -p "Enter username/email: " USERNAME
    fi

    if [ -z "$PASSWORD" ]; then
        # Prompt user for input
        PASSWORD=$(get_password "Enter password: ")
    fi

    # Validate inputs
    if [ -z "$USERNAME" ] || [ -z "$PASSWORD" ]; then
        print_error "Username and password are required"
        exit 1
    fi

    # Show configuration
    print_info "Configuration:"
    print_info "  Username: $USERNAME"
    print_info "  User ID (if specified): $USER_ID"
    print_info "  API URL: $API_BASE_URL"
    print_info "  Output Directory: $OUTPUT_DIR"
    if [ -n "$SUBMISSION_ID" ]; then
        print_info "  Submission ID: $SUBMISSION_ID"
    fi

    # Run the Python script
    if [ -n "$SUBMISSION_ID" ]; then
        print_info "Downloading submission by ID..."
    else
        print_info "Running submission lister..."
    fi

    API_BASE_URL="$API_BASE_URL" python3 "$PYTHON_SCRIPT" \
        --username "$USERNAME" \
        --password "$PASSWORD" \
        --output "$OUTPUT_DIR" \
        ${SUBMISSION_ID:+--submission-id "$SUBMISSION_ID"} \
        ${USER_ID:+--user-id "$USER_ID"} \
        $VERBOSE

    local exit_code=$?

    if [ $exit_code -eq 0 ]; then
        print_success "Script completed successfully!"
    else
        print_error "Script failed with exit code: $exit_code"
        exit $exit_code
    fi

    # Extract downloaded tar.gz file from download folder to the output directory
    DOWNLOAD_DIR="$SCRIPT_DIR/download"
    TAR_FILE=$(find "$DOWNLOAD_DIR" -name "*.tar.gz" -type f | head -1)

    if [ -z "$TAR_FILE" ]; then
        print_error "No tar.gz file found in download directory: $DOWNLOAD_DIR"
        exit 1
    fi

    # Remove existing aichallenge_submit directory
    print_info "Removing existing aichallenge_submit directory..."
    rm -rf "$OUTPUT_DIR/aichallenge_submit"
    print_success "Removal completed successfully!"

    print_info "Extracting downloaded tar.gz file to the output directory..."
    print_info "Source: $TAR_FILE"
    print_info "Destination: $OUTPUT_DIR"

    if tar -xzf "$TAR_FILE" -C "$OUTPUT_DIR"; then
        print_success "Extraction completed successfully!"

        # Clean up the original tar.gz file
        print_info "Cleaning up original tar.gz file..."
        rm -f "$TAR_FILE"
        print_success "Cleanup completed successfully!"
    else
        print_error "Extraction failed!"
        exit 1
    fi
}

# Run main function
main "$@"
