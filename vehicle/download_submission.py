#!/usr/bin/env python3
"""
Command-line script to list recent submissions and allow user to select one for download
Usage: python3 scripts/list_submissions.py --username=user@example.com --password=mypassword --output=./downloads/
"""

import os
import sys
import json
import argparse
import requests
import logging
from datetime import datetime
from typing import List, Dict, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SubmissionLister:
    def __init__(self):
        """Initialize the submission lister with configuration"""
        self.api_base_url = os.getenv('API_BASE_URL', 'http://localhost:8000')

        # Initialize requests session
        self.session = requests.Session()
        self.session.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })

    def authenticate(self, username: str, password: str) -> Optional[str]:
        """
        Authenticate with AWS Cognito using client-side authentication

        Args:
            username (str): Cognito username/email
            password (str): Cognito password

        Returns:
            str: Access token if successful, None otherwise
        """
        try:
            logger.info("Authenticating with AWS Cognito...")

            auth_url = f"{self.api_base_url}/api/user/login"

            auth_data = {
                'username': username,
                'password': password
            }

            response = self.session.post(auth_url, json=auth_data, timeout=30)

            if response.status_code == 200:
                data = response.json()
                if 'AccessToken' in data:
                    logger.info("Authentication successful!")
                    return data['AccessToken']
                else:
                    logger.error("No access token in response")
                    return None
            else:
                logger.error(f"Authentication failed: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return None

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during authentication: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return None

    def list_recent_submissions(self, access_token: str, user_id: str = None) -> Optional[List[Dict]]:
        """
        Get the list of recent submissions from the API

        Args:
            access_token (str): Valid access token from Cognito
            user_id (str): Optional user ID to download submissions for
        Returns:
            List[Dict]: List of submission data if successful, None otherwise
        """
        try:
            logger.info("Fetching recent submissions...")

            # Set authorization header
            headers = {'Authorization': f'Bearer {access_token}'}

            # Make API request
            url = f"{self.api_base_url}/api/submission/list"
            if user_id:
                url += f"?user_id={user_id}"
            response = self.session.get(url, headers=headers, timeout=30)

            if response.status_code != 200:
                logger.error(f"API Error: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return None

            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.error("Invalid JSON response from API")
                return None

            if not data or 'submissions' not in data:
                logger.error("Invalid response from API")
                return None

            submissions = data['submissions']
            total_count = data.get('total_count', len(submissions))
            returned_count = data.get('returned_count', len(submissions))

            logger.info(f"Found {returned_count} recent submissions (out of {total_count} total)")
            return submissions

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error while fetching submissions: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Error fetching submissions: {str(e)}")
            return None

    def format_file_size(self, size_bytes: int) -> str:
        """Convert bytes to human readable format"""
        if size_bytes == 0:
            return "0 B"

        size_names = ["B", "KB", "MB", "GB"]
        i = 0
        while size_bytes >= 1024 and i < len(size_names) - 1:
            size_bytes /= 1024.0
            i += 1

        return f"{size_bytes:.1f} {size_names[i]}"

    def format_timestamp(self, timestamp: int) -> str:
        """Convert timestamp to readable date format"""
        try:
            dt = datetime.fromtimestamp(timestamp)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            return "Invalid date"

    def display_submissions(self, submissions: List[Dict], user_id: str = None) -> None:
        """
        Display submissions in a formatted table

        Args:
            submissions (List[Dict]): List of submission data
        """
        if not submissions:
            print("\n❌ No submissions found.")
            return

        print("\n" + "="*140)
        print("📋 RECENT SUBMISSIONS", end="")
        if user_id:
            print(f" for User ID: {user_id}")
        else:
            print()
        print("="*140)
        print(f"{'#':<3} {'Submission ID':<36} {'Date':<20} {'User ID':<15} {'Comment':<50}")
        print("-"*140)

        for i, submission in enumerate(submissions, 1):
            source_file_path = submission.get('sourceFilePath', 'Unknown')
            submission_id = source_file_path.split('/')[-1].split('.')[0]
            submission_time_ms = submission.get('submission_time')
            submission_time = self.format_timestamp(submission_time_ms / 1000) if submission_time_ms else 'Unknown'
            user_id = submission.get('userId', 'Unknown')
            comment = submission.get('buildComment', '')

            # Remove newlines from comment
            comment = comment.replace('\n', ' ')

            # Truncate long comments
            if len(comment) > 50:
                comment = comment[:50] + "..."

            print(f"{i:<3} {submission_id:36} {submission_time:<20} {user_id:<15} {comment:<50}")

        print("-"*140)
        print(f"Total: {len(submissions)} submissions")
        print("="*140)

    def get_user_selection(self, submissions: List[Dict]) -> Optional[Dict]:
        """
        Get user selection for which submission to download

        Args:
            submissions (List[Dict]): List of submission data

        Returns:
            Dict: Selected submission data, or None if invalid selection
        """
        if not submissions:
            return None

        while True:
            try:
                print(f"\n🔢 Choose an option:")
                print(f"   • Enter a number (1-{len(submissions)}) to download that submission")
                print("   • Enter 'id:SUBMISSION_ID' to download a specific submission by ID (direct API call)")
                print("   • Enter 'q' to quit")

                choice = input("Your choice: ").strip()

                if choice == 'q':
                    print("👋 Goodbye!")
                    return None

                # Check if user wants to download by submission ID
                if choice.startswith('id:'):
                    submission_id = choice[3:].strip()
                    if not submission_id:
                        print("❌ Please provide a submission ID after 'id:'")
                        continue

                    # Return a special dict indicating direct download by ID
                    print(f"\n✅ Will download submission by ID: {submission_id}")
                    return {'submission_id': submission_id, 'direct_download': True}

                # Try to parse as index number
                index = int(choice) - 1

                if 0 <= index < len(submissions):
                    selected = submissions[index]
                    print(f"\n✅ Selected: {selected.get('source_file_path', 'Unknown')}")
                    return selected
                else:
                    print(f"❌ Invalid choice. Please enter a number between 1 and {len(submissions)}")

            except ValueError:
                print("❌ Invalid input. Please enter a number, 'id:SUBMISSION_ID', or 'q' to quit.")
            except KeyboardInterrupt:
                print("\n👋 Goodbye!")
                return None

    def download_submission(self, access_token: str, submission: Dict, output_dir: str = './downloads/', user_id: str = None) -> bool:
        """
        Download a specific submission file

        Args:
            access_token (str): Valid access token from Cognito
            submission (Dict): Submission data containing source_file_path
            output_dir (str): Directory to save the downloaded file

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            source_file_path = submission.get('sourceFilePath')
            submission_id = source_file_path.split('/')[-1].split('.')[0]

            if not submission_id:
                logger.error("No submission ID found in submission data")
                return False

            logger.info(f"Downloading submission: {submission_id}")

            # Set authorization header
            headers = {'Authorization': f'Bearer {access_token}'}

            # Make API request to download specific submission
            url = f"{self.api_base_url}/api/submission/download/{submission_id}"
            if user_id:
                url += f"?user_id={user_id}"
            response = self.session.get(url, headers=headers, timeout=30)

            if response.status_code != 200:
                logger.error(f"API Error: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False

            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.error("Invalid JSON response from API")
                return False

            if not data or 'download_url' not in data:
                logger.error("Invalid response from API")
                return False

            logger.info(f"Found submission file: {data['filename']}")
            logger.info(f"File size: {data['file_size']:,} bytes")

            if data.get('comment'):
                logger.info(f"Comment: {data['comment']}")

            # Create download directory inside vehicle folder
            script_dir = os.path.dirname(os.path.abspath(__file__))
            download_dir = os.path.join(script_dir, 'download')
            os.makedirs(download_dir, exist_ok=True)

            # Download the file using the pre-signed URL to download folder
            download_url = data['download_url']
            output_path = os.path.join(download_dir, data['filename'])

            logger.info(f"Downloading file to: {output_path}")

            # Download file with progress tracking
            download_response = requests.get(download_url, stream=True, timeout=300)

            if download_response.status_code != 200:
                logger.error(f"Failed to download file. HTTP Code: {download_response.status_code}")
                return False

            # Write file with progress tracking
            total_size = int(download_response.headers.get('content-length', 0))
            downloaded_size = 0

            with open(output_path, 'wb') as f:
                for chunk in download_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)

                        # Show progress for large files
                        if total_size > 0 and downloaded_size % (1024 * 1024) == 0:  # Every MB
                            progress = (downloaded_size / total_size) * 100
                            logger.info(f"Download progress: {progress:.1f}% ({downloaded_size:,}/{total_size:,} bytes)")

            logger.info(f"✅ Download completed successfully!")
            logger.info(f"📁 File saved to: {output_path}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during download: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Download error: {str(e)}")
            return False

    def download_by_id(self, access_token: str, submission_id: str, output_dir: str = './downloads/', user_id: str = None) -> bool:
        """
        Download a submission by its ID directly

        Args:
            access_token (str): Valid access token from Cognito
            submission_id (str): Submission ID to download
            output_dir (str): Directory to save downloaded files

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            logger.info(f"Downloading submission by ID: {submission_id}")

            # Set authorization header
            headers = {'Authorization': f'Bearer {access_token}'}

            # Make API request to download specific submission
            url = f"{self.api_base_url}/api/submission/download/{submission_id}"
            if user_id:
                url += f"?user_id={user_id}"
            response = self.session.get(url, headers=headers, timeout=30)

            if response.status_code != 200:
                logger.error(f"API Error: HTTP {response.status_code}")
                logger.error(f"Response: {response.text}")
                return False

            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.error("Invalid JSON response from API")
                return False

            if not data or 'download_url' not in data:
                logger.error("Invalid response from API")
                return False

            logger.info(f"Found submission file: {data['filename']}")
            logger.info(f"File size: {data['file_size']:,} bytes")

            if data.get('comment'):
                logger.info(f"Comment: {data['comment']}")

            # Create download directory inside vehicle folder
            script_dir = os.path.dirname(os.path.abspath(__file__))
            download_dir = os.path.join(script_dir, 'download')
            os.makedirs(download_dir, exist_ok=True)

            # Download the file using the pre-signed URL to download folder
            download_url = data['download_url']
            output_path = os.path.join(download_dir, data['filename'])

            logger.info(f"Downloading file to: {output_path}")

            # Download file with progress tracking
            download_response = requests.get(download_url, stream=True, timeout=300)

            if download_response.status_code != 200:
                logger.error(f"Failed to download file. HTTP Code: {download_response.status_code}")
                return False

            # Write file with progress tracking
            total_size = int(download_response.headers.get('content-length', 0))
            downloaded_size = 0

            with open(output_path, 'wb') as f:
                for chunk in download_response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)

                        # Show progress for large files
                        if total_size > 0 and downloaded_size % (1024 * 1024) == 0:  # Every MB
                            progress = (downloaded_size / total_size) * 100
                            logger.info(f"Download progress: {progress:.1f}% ({downloaded_size:,}/{total_size:,} bytes)")

            logger.info(f"✅ Download completed successfully!")
            logger.info(f"📁 File saved to: {output_path}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Network error during download: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Download error: {str(e)}")
            return False

    def run(self, username: str, password: str, output_dir: str = './downloads/', submission_id: str = None, user_id: str = None) -> bool:
        """
        Main execution method

        Args:
            username (str): Cognito username/email
            password (str): Cognito password
            output_dir (str): Directory to save downloaded files
            submission_id (str): Optional submission ID to download directly
            user_id (str): Optional user ID to download submissions for
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Authenticate
            access_token = self.authenticate(username, password)
            if not access_token:
                logger.error("Authentication failed")
                return False

            # If submission_id is provided, download directly
            if submission_id:
                logger.info(f"Downloading submission by ID: {submission_id}")
                return self.download_by_id(access_token, submission_id, output_dir, user_id)

            # List submissions
            submissions = self.list_recent_submissions(access_token, user_id)
            if not submissions:
                logger.error("Failed to fetch submissions")
                return False

            # Display submissions
            self.display_submissions(submissions, user_id)

            # Get user selection
            selected_submission = self.get_user_selection(submissions)
            if not selected_submission:
                return True  # User chose to quit

            # Check if this is a direct download by ID
            if selected_submission.get('direct_download'):
                # Download directly by ID (bypasses the submissions array)
                success = self.download_by_id(access_token, selected_submission['submission_id'], output_dir)
            else:
                # Download from the submissions array
                success = self.download_submission(access_token, selected_submission, output_dir, user_id)
            return success

        except KeyboardInterrupt:
            logger.info("\n👋 Operation cancelled by user")
            return False
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}")
            return False

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='List recent submissions and download selected one, or download by submission ID',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List submissions and choose interactively
  python3 scripts/list_submissions.py --username=user@example.com --password=mypassword

  # Download specific submission by ID
  python3 scripts/list_submissions.py --username=user@example.com --password=mypassword --submission-id=abc123

  # With custom output directory
  python3 scripts/list_submissions.py --username=user@example.com --password=mypassword --output=./my_downloads/

  # With custom API URL
  API_BASE_URL=https://api.example.com python3 scripts/list_submissions.py --username=user@example.com --password=mypassword
        """
    )

    parser.add_argument(
        '--username',
        required=True,
        help='AWS Cognito username/email'
    )

    parser.add_argument(
        '--password',
        required=True,
        help='AWS Cognito password'
    )

    parser.add_argument(
        '--output',
        default='./downloads/',
        help='Output directory for downloaded files (default: ./downloads/)'
    )

    parser.add_argument(
        '--submission-id',
        help='Download specific submission by ID (skips listing)'
    )

    parser.add_argument(
        '--user-id',
        help='User ID (only available for admin users)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Create and run the submission lister
    lister = SubmissionLister()
    success = lister.run(args.username, args.password, args.output, args.submission_id, args.user_id)

    if success:
        logger.info("🎉 Operation completed successfully!")
        sys.exit(0)
    else:
        logger.error("❌ Operation failed")
        sys.exit(1)

if __name__ == '__main__':
    main()
