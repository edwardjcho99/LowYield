#!/usr/bin/env python3
"""
Canvas API Scraper - Download course materials and files from Canvas LMS
"""

import os
import requests
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed

class CanvasScraper:
    def __init__(self, canvas_url: str, api_token: str):
        """
        Initialize Canvas API client
        
        Args:
            canvas_url: Your Canvas instance URL (e.g., https://canvas.instructure.com)
            api_token: Your Canvas API token
        """
        self.canvas_url = canvas_url.rstrip('/')
        self.api_token = api_token
        self.headers = {
            'Authorization': f'Bearer {api_token}'
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.base_url = f'{self.canvas_url}/api/v1'

    def validate_credentials(self) -> None:
        """Verify the API token is valid. Raises requests.HTTPError on failure."""
        response = self.session.get(f'{self.base_url}/users/self')
        response.raise_for_status()

    def sanitize_path_component(self, value: str, fallback: str = 'Untitled') -> str:
        """Sanitize a directory/file name component to avoid nested paths and invalid characters."""
        if not value:
            return fallback
        safe_value = "".join(c for c in value if c.isalnum() or c in (' ', '-', '_')).strip()
        return safe_value or fallback
        
    def get_courses(self) -> list:
        """Get all courses for current user"""
        url = f'{self.base_url}/courses'
        params = {'per_page': 100}
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            # Filter out invalid/placeholder courses without a name
            courses = response.json()
            return [c for c in courses if c.get('name')]
        except requests.exceptions.RequestException as e:
            print(f"Error fetching courses: {e}")
            return []
    
    
    def get_course_modules(self, course_id: int) -> list:
        """Get all modules in a course"""
        url = f'{self.base_url}/courses/{course_id}/modules'
        params = {'per_page': 100}
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching modules for course {course_id}: {e}")
            return []
    
    def get_module_items(self, course_id: int, module_id: int) -> list:
        """Get all items in a module"""
        url = f'{self.base_url}/courses/{course_id}/modules/{module_id}/items'
        params = {'per_page': 100}
        
        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching module items: {e}")
            return []
    
    def get_module_details(self, course_id: int, module_id: int) -> dict:
        """Get all fields and details for a specific module"""
        url = f'{self.base_url}/courses/{course_id}/modules/{module_id}'
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching module details: {e}")
            return {}
    
    def print_module_fields(self, course_id: int, module_id: int):
        """Print all fields in a module"""
        module_details = self.get_module_details(course_id, module_id)
        
        if not module_details:
            print("No module details found.")
            return
        
        print(f"\n📋 Module Fields:")
        print("-" * 50)
        
        for key, value in module_details.items():
            # Format the output nicely
            if isinstance(value, dict):
                print(f"{key}:")
                for sub_key, sub_value in value.items():
                    print(f"  {sub_key}: {sub_value}")
            elif isinstance(value, list):
                print(f"{key}: [List with {len(value)} items]")
            else:
                print(f"{key}: {value}")
        
        print("-" * 50)
    
    def get_page_details(self, course_id: int, page_id: str) -> dict:
        """Get details for a specific page"""
        url = f'{self.base_url}/courses/{course_id}/pages/{page_id}'
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching page details: {e}")
            return {}
    
    def download_page(self, course_id: int, page_id: str, output_path: str) -> bool:
        """Download a page as HTML file"""
        page_details = self.get_page_details(course_id, page_id)
        
        if not page_details:
            print(f"Could not get page details for {page_id}")
            return False
        
        try:
            # Create directory if needed
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            # Get page content and title
            page_title = page_details.get('title', 'Untitled')
            page_body = page_details.get('body', '')
            
            # Create HTML file
            html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{page_title}</title>
</head>
<body>
    <h1>{page_title}</h1>
    <div>{page_body}</div>
</body>
</html>"""
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(html_content)
            
            print(f"✓ Downloaded: {output_path}")
            return True
            
        except Exception as e:
            print(f"Error saving page: {e}")
            return False
    
    def extract_pdf_links(self, html_content: str, course_id: int) -> list:
        """Extract all PDF links from HTML content and resolve them through Canvas API"""
        soup = BeautifulSoup(html_content, 'html.parser')
        pdf_links = []
        
        # Find all links in the page
        for link in soup.find_all('a', href=True):
            href = link.get('href', '').strip()
            link_text = link.get_text(strip=True) or 'PDF Document'
            
            if not href:
                continue
            
            # Check if this is a potential PDF link
            is_pdf = (
                href.endswith('.pdf') or
                '/pdf' in href.lower() or
                '/files/' in href or
                'download' in href.lower()
            )
            
            if is_pdf:
                # Try to resolve the link
                resolved_url = self.resolve_canvas_link(href, course_id)
                if resolved_url:
                    pdf_links.append({
                        'url': resolved_url,
                        'text': link_text
                    })
        
        # Debug: print found links
        if pdf_links:
            print(f"  Debug: Found {len(pdf_links)} resolved PDF link(s):")
            for link in pdf_links:
                print(f"    - {link['text']}: {link['url']}")
        
        return pdf_links
    
    def resolve_canvas_link(self, href: str, course_id: int) -> str:
        """Resolve a Canvas link to an actual download URL"""
        try:
            # If it's already a full URL, return it
            if href.startswith('http'):
                return href
            
            # Handle Canvas file URLs like /files/123 or /files/123/download
            if '/files/' in href:
                # Extract file ID from URL
                parts = href.split('/files/')
                if len(parts) > 1:
                    file_id_part = parts[1].split('/')[0]
                    try:
                        file_id = int(file_id_part)
                        # Try to get file info through API
                        file_response = self.session.get(
                            f'{self.base_url}/files/{file_id}',
                            timeout=5
                        )
                        if file_response.status_code == 200:
                            file_data = file_response.json()
                            return file_data.get('url', f"{self.canvas_url}{href}")
                    except:
                        pass
            
            # Handle relative URLs
            if href.startswith('/'):
                return f"{self.canvas_url}{href}"
            
            # Handle relative URLs without leading slash
            return f"{self.canvas_url}/{href}"
            
        except Exception as e:
            print(f"  Error resolving link {href}: {e}")
            # Return original URL as fallback
            if href.startswith('/'):
                return f"{self.canvas_url}{href}"
            elif not href.startswith('http'):
                return f"{self.canvas_url}/{href}"
            return href
    
    def resolve_final_url(self, url: str) -> str:
        """Resolve URL by making a HEAD request to follow redirects"""
        try:
            # Make URL absolute if needed
            if url.startswith('/'):
                url = f"{self.canvas_url}{url}"
            elif not url.startswith('http'):
                url = urljoin(self.canvas_url, url)
            
            print(f"  Resolving URL: {url[:80]}...")
            
            # Make a HEAD request to follow redirects and get final URL
            response = self.session.head(url, allow_redirects=True, timeout=10)
            
            # Return the final URL after following redirects
            return response.url
            
        except Exception as e:
            print(f"  Error resolving URL: {e}")
            if url.startswith('/'):
                return f"{self.canvas_url}{url}"
            elif not url.startswith('http'):
                return f"{self.canvas_url}/{url}"
            return url
    
    def download_pdf_from_url(self, pdf_url: str, output_path: str, course_id: str = None) -> bool:
        """Download a PDF from a URL using Canvas API (course-specific endpoint)"""
        try:
            download_url = pdf_url
            print(f"  Original URL: {pdf_url}")

            # Use a dedicated session per download to avoid thread-safety issues
            with requests.Session() as session:
                session.headers.update(self.headers)

                # Extract course ID from URL if not provided
                if not course_id and '/courses/' in pdf_url:
                    parts = pdf_url.split('/courses/')
                    if len(parts) > 1:
                        course_id_part = parts[1].split('/')[0]
                        course_id = course_id_part
                        print(f"  Extracted course ID from URL: {course_id}")

                # Try to extract file ID from the URL
                if '/files/' in pdf_url:
                    parts = pdf_url.split('/files/')
                    if len(parts) > 1:
                        file_id_part = parts[1].split('?')[0].split('/')[0]
                        print(f"  Extracted file ID: {file_id_part}")

                        # Try course-specific file endpoint first (Option C)
                        if course_id:
                            api_url = f'{self.base_url}/courses/{course_id}/files/{file_id_part}'
                            print(f"  Trying course-specific endpoint: {api_url}")

                            try:
                                api_response = session.get(api_url, timeout=10)
                                print(f"  API response status: {api_response.status_code}")

                                if api_response.status_code == 200:
                                    file_data = api_response.json()
                                    print(f"  API returned file data keys: {list(file_data.keys())}")

                                    # Try different field names for download URL
                                    api_url_from_response = file_data.get('url') or file_data.get('download_url')
                                    if api_url_from_response:
                                        print(f"  Got API download URL: {api_url_from_response[:100]}...")
                                        download_url = api_url_from_response
                                    else:
                                        print(f"  ERROR: No URL field in response. Keys: {list(file_data.keys())}")
                                        print(f"  Full response: {file_data}")
                                else:
                                    print(f"  Course endpoint returned {api_response.status_code}, trying global endpoint...")

                                    # Fall back to global endpoint
                                    api_url = f'{self.base_url}/files/{file_id_part}'
                                    print(f"  Trying global endpoint: {api_url}")
                                    api_response = session.get(api_url, timeout=10)
                                    print(f"  Global API response status: {api_response.status_code}")

                                    if api_response.status_code == 200:
                                        file_data = api_response.json()
                                        api_url_from_response = file_data.get('url') or file_data.get('download_url')
                                        if api_url_from_response:
                                            download_url = api_url_from_response
                                            print(f"  Got URL from global endpoint: {download_url[:100]}...")
                            except requests.exceptions.RequestException as e:
                                print(f"  API request failed: {e}")
                            except Exception as e:
                                print(f"  Error during API call: {type(e).__name__}: {e}")
                else:
                    print(f"  WARNING: '/files/' not found in URL")

                # If we still don't have a working URL, try adding download parameter
                if download_url == pdf_url:
                    if '?' not in download_url:
                        download_url = pdf_url + '?download=1'
                    else:
                        download_url = pdf_url + '&download=1'
                    print(f"  Using fallback URL with download parameter")

                print(f"  Final Download URL: {download_url[:100]}...")

                # Download the PDF with proper headers to avoid WAF issues
                headers_for_download = self.headers.copy()
                headers_for_download['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                print(f"  Initiating download with headers: {list(headers_for_download.keys())}")

                response = session.get(
                    download_url,
                    headers=headers_for_download,
                    stream=True,
                    timeout=10,
                    allow_redirects=True
                )

                print(f"  Response status code: {response.status_code}")
                print(f"  Response URL (after redirects): {response.url}")

                # Check for WAF challenge or redirect
                waf_action = response.headers.get('x-amzn-waf-action', '')
                if waf_action:
                    print(f"  ⚠️  WAF Challenge detected: {waf_action}")

                if 'cross_domain_login' in response.url or 'login' in response.url.lower():
                    print(f"  ✗ Redirected to login page - Session/authentication may have expired")
                    print(f"  NOTE: Canvas WAF requires active session for file downloads")
                    return False

                # Check content type and size
                content_type = response.headers.get('content-type', '')
                content_length = response.headers.get('content-length', '0')

                print(f"  Content-Type: {content_type}")
                print(f"  Content-Length: {content_length}")

                # Read first few bytes to check for PDF magic number
                first_bytes = response.content[:10] if response.content else b''
                print(f"  First bytes of response: {first_bytes[:10]}")
                is_pdf_magic = first_bytes.startswith(b'%PDF')
                is_pdf_type = 'pdf' in content_type.lower()

                print(f"  Is PDF by magic bytes: {is_pdf_magic}")
                print(f"  Is PDF by content-type: {is_pdf_type}")

                # Verify it's a PDF
                if not (is_pdf_type or is_pdf_magic):
                    print(f"  ✗ URL does not point to a PDF (Content-Type: {content_type})")
                    if response.content:
                        preview = response.text[:200] if hasattr(response, 'text') else str(response.content[:100])
                        print(f"  Response preview: {preview}")
                    return False

                # Create directory and download file
                Path(output_path).parent.mkdir(parents=True, exist_ok=True)

                bytes_written = 0
                with open(output_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            bytes_written += len(chunk)

                print(f"  ✓ Downloaded PDF: {output_path} ({bytes_written} bytes)")
                return True
        except requests.exceptions.HTTPError as e:
            print(f"  ✗ HTTP Error: {e.response.status_code} - {e.response.reason}")
            if hasattr(e.response, 'text'):
                print(f"  Response: {e.response.text[:200]}")
            return False
        except requests.exceptions.Timeout as e:
            print(f"  ✗ Request timeout: {e}")
            return False
        except requests.exceptions.ConnectionError as e:
            print(f"  ✗ Connection error: {e}")
            return False
        except Exception as e:
            print(f"  ✗ Error downloading PDF: {type(e).__name__}: {e}")
            import traceback
            print(f"  Traceback: {traceback.format_exc()}")
            return False
            
            print(f"  Response status code: {response.status_code}")
            print(f"  Response URL (after redirects): {response.url}")
            
            # Check for WAF challenge or redirect
            waf_action = response.headers.get('x-amzn-waf-action', '')
            if waf_action:
                print(f"  ⚠️  WAF Challenge detected: {waf_action}")
            
            if 'cross_domain_login' in response.url or 'login' in response.url.lower():
                print(f"  ✗ Redirected to login page - Session/authentication may have expired")
                print(f"  NOTE: Canvas WAF requires active session for file downloads")
                return False
            
            # Check content type and size
            content_type = response.headers.get('content-type', '')
            content_length = response.headers.get('content-length', '0')
            
            print(f"  Content-Type: {content_type}")
            print(f"  Content-Length: {content_length}")
            
            # Read first few bytes to check for PDF magic number
            first_bytes = response.content[:10] if response.content else b''
            print(f"  First bytes of response: {first_bytes[:10]}")
            is_pdf_magic = first_bytes.startswith(b'%PDF')
            is_pdf_type = 'pdf' in content_type.lower()
            
            print(f"  Is PDF by magic bytes: {is_pdf_magic}")
            print(f"  Is PDF by content-type: {is_pdf_type}")
            
            # Verify it's a PDF
            if not (is_pdf_type or is_pdf_magic):
                print(f"  ✗ URL does not point to a PDF (Content-Type: {content_type})")
                if response.content:
                    preview = response.text[:200] if hasattr(response, 'text') else str(response.content[:100])
                    print(f"  Response preview: {preview}")
                return False
            
            # Create directory and download file
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            
            bytes_written = 0
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)
            
            print(f"  ✓ Downloaded PDF: {output_path} ({bytes_written} bytes)")
            return True
            
        except requests.exceptions.HTTPError as e:
            print(f"  ✗ HTTP Error: {e.response.status_code} - {e.response.reason}")
            if hasattr(e.response, 'text'):
                print(f"  Response: {e.response.text[:200]}")
            return False
        except requests.exceptions.Timeout as e:
            print(f"  ✗ Request timeout: {e}")
            return False
        except requests.exceptions.ConnectionError as e:
            print(f"  ✗ Connection error: {e}")
            return False
        except Exception as e:
            print(f"  ✗ Error downloading PDF: {type(e).__name__}: {e}")
            import traceback
            print(f"  Traceback: {traceback.format_exc()}")
            return False
    
    def matches_filter(self, pdf_name: str, filters: list) -> bool:
        """Check if a PDF name matches any of the selected filters using fuzzy matching
        
        Supports variations and typos, e.g.:
        - "slideset_single" matches "slides_single", "slide_single", "slideset single"
        - "learning_objectives" matches "learning objectives", "learningobjectives", etc.
        - Typos within ~80% similarity are tolerated
        
        Args:
            pdf_name: Name of the PDF file to check
            filters: List of filter keywords
        
        Returns:
            True if PDF matches any filter, False otherwise
        """
        if not filters:
            return True  # If no filters, include all
        
        # Define keyword variations for each filter type
        filter_keywords = {
            'learning_objectives': [
                'learning objective', 'learning objectives', 'learning outcomes',
                'objectives', 'learningobjective', 'learningobjectives'
            ],
            'slideset_single': [
                'slideset single', 'slideset_single', 'slides single', 'slides_single',
                'slide single', 'slide_single', 'slidesets single', 'slidesets_single',
                'slide set single', 'slideset-single', 'slides-single'
            ],
            'slideset_multiple': [
                'slideset multi', 'slideset_multi', 'slideset multiple', 'slideset_multiple',
                'slides multi', 'slides_multi', 'slide multi', 'slide_multi',
                'slidesets multi', 'slidesets_multi', 'slides multiple', 'slide set multi',
                'slideset-multi', 'slideset-multiple', 'slides-multi'
            ],
            'studyguide': [
                'study guide', 'study_guide', 'studyguide', 'study guide',
                'studyguides', 'study guides', 'study-guide'
            ],
            'transcript': [
                'transcript', 'transcripts', 'trans'
            ],
            'handouts': [
                'handout', 'handouts', 'hand out', 'hand-out', 'handout packet'
            ]
        }
        
        pdf_name_lower = pdf_name.lower()
        # Normalize the PDF name: remove extensions, replace hyphens/underscores with spaces
        pdf_name_normalized = pdf_name_lower.replace('.pdf', '').replace('_', ' ').replace('-', ' ')
        
        for filter_key in filters:
            if filter_key not in filter_keywords:
                print(f"  [DEBUG] Filter key '{filter_key}' not found in filter_keywords")
                continue
            
            # For slideset filters, require explicit type (single vs multi) to avoid ambiguous 'slideset' matches
            if filter_key == 'slideset_single':
                if 'multi' in pdf_name_normalized or 'multiple' in pdf_name_normalized:
                    print(f"  [DEBUG] Skipping slideset_single match because name suggests multiple: {pdf_name_normalized}")
                    continue
                if 'single' not in pdf_name_normalized and '1' not in pdf_name_normalized:
                    print(f"  [DEBUG] Skipping slideset_single match because name lacks 'single' indicator: {pdf_name_normalized}")
                    continue
            if filter_key == 'slideset_multiple':
                if 'single' in pdf_name_normalized:
                    print(f"  [DEBUG] Skipping slideset_multiple match because name suggests single: {pdf_name_normalized}")
                    continue
                if 'multi' not in pdf_name_normalized and 'multiple' not in pdf_name_normalized:
                    print(f"  [DEBUG] Skipping slideset_multiple match because name lacks 'multi' indicator: {pdf_name_normalized}")
                    continue

            keywords = filter_keywords[filter_key]
            
            # Check for exact substring matches (most efficient)
            for keyword in keywords:
                keyword_normalized = keyword.replace('_', ' ').replace('-', ' ')
                if keyword_normalized in pdf_name_normalized:
                    print(f"  [DEBUG] MATCH: '{keyword_normalized}' found in '{pdf_name_normalized}'")
                    return True
            
            # Check for fuzzy matches if no exact match found
            for keyword in keywords:
                keyword_normalized = keyword.replace('_', ' ').replace('-', ' ')
                
                # Split into words and check similarity
                pdf_words = pdf_name_normalized.split()
                keyword_words = keyword_normalized.split()
                
                # Calculate similarity using SequenceMatcher
                similarity = SequenceMatcher(None, pdf_name_normalized, keyword_normalized).ratio()
                
                # Also check individual word matches
                for pdf_word in pdf_words:
                    for keyword_word in keyword_words:
                        # Avoid false positives on very short words (e.g., "and" vs "hand")
                        if len(pdf_word) < 4 or len(keyword_word) < 4:
                            continue
                        word_similarity = SequenceMatcher(None, pdf_word, keyword_word).ratio()
                        if word_similarity >= 0.85:  # 85% similarity tolerance for typos
                            print(f"  [DEBUG] FUZZY WORD MATCH: '{pdf_word}' ~= '{keyword_word}' ({word_similarity:.2f})")
                            return True
                
                # Accept if overall similarity is high
                if similarity >= 0.85:  # 85% overall similarity
                    print(f"  [DEBUG] FUZZY OVERALL MATCH: '{pdf_name_normalized}' ~= '{keyword_normalized}' ({similarity:.2f})")
                    return True
        
        print(f"  [DEBUG] NO MATCH for '{pdf_name}' against filters: {filters}")
        return False
    
    def download_pages_and_pdfs_from_module(
        self,
        course_id: int,
        module_id: int,
        output_dir: str = './module_pages',
        filters: list = None,
        max_workers: int = 6,
    ):
        """Download all page items and PDFs within them from a module
        
        Args:
            course_id: Canvas course ID
            module_id: Canvas module ID
            output_dir: Directory to save files
            filters: List of filter keywords (e.g., ['learning objectives', 'slideset_single'])
                    If None or empty, downloads all PDFs
            max_workers: Maximum number of concurrent downloads to run at once.
        """
        if filters is None:
            filters = []
        
        print(f"\n[DEBUG] download_pages_and_pdfs_from_module called with filters: {filters} (max_workers={max_workers})")
        print(f"[DEBUG] Filter is None: {filters is None}, Filter is empty: {len(filters) == 0}")
        items = self.get_module_items(course_id, module_id)
        
        if not items:
            print("No items found in this module.")
            return
        
        # Filter for pages only
        page_items = [item for item in items if item.get('type') == 'Page']
        
        if not page_items:
            print("No page items found in this module.")
            return
        
        print(f"\n📥 Downloading {len(page_items)} page(s) and PDFs from module...")
        
        # Get course name for directory
        try:
            course_response = self.session.get(f'{self.base_url}/courses/{course_id}')
            course_response.raise_for_status()
            course = course_response.json()
            course_name = course.get('name', f'Course_{course_id}')
        except:
            course_name = f'Course_{course_id}'
        course_name = self.sanitize_path_component(course_name, f'Course_{course_id}')
        
        # Get module name
        try:
            module_response = self.session.get(f'{self.base_url}/courses/{course_id}/modules/{module_id}')
            module_response.raise_for_status()
            module = module_response.json()
            module_name = module.get('name', f'Module_{module_id}')
        except:
            module_name = f'Module_{module_id}'
        module_name = self.sanitize_path_component(module_name, f'Module_{module_id}')
        
        pdfs_downloaded = 0
        
        for item in page_items:
            page_id = item.get('page_url', item.get('id', ''))
            page_title = item.get('title', 'Untitled')
            
            # Create safe filename
            safe_title = self.sanitize_path_component(page_title, f'Page_{page_id}')
            
            # Get page details
            page_details = self.get_page_details(course_id, page_id)
            
            if not page_details:
                continue
            
            # Get page content and title
            page_body = page_details.get('body', '')
            
            # Create directory structure with subdirectory for each page (for PDFs)
            page_dir = Path(output_dir) / course_name / module_name / safe_title
            
            try:
                page_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                print(f"✗ Error creating directory: {e}")
                continue
            
            # Extract and download PDFs from page
            pdf_links = self.extract_pdf_links(page_body, course_id)
            
            if pdf_links:
                print(f"  Found {len(pdf_links)} PDF(s) in page: {page_title}")
                download_tasks = []

                for pdf_link in pdf_links:
                    pdf_url = pdf_link['url']
                    pdf_text = pdf_link['text']

                    # Check if PDF matches selected filters
                    matches = self.matches_filter(pdf_text, filters)
                    print(f"  [DEBUG] PDF: '{pdf_text}' | Filters: {filters} | Matches: {matches}")
                    if not matches:
                        print(f"  ⊘ Skipping PDF (doesn't match filters): {pdf_text}")
                        continue

                    # Create safe filename from link text
                    safe_pdf_name = "".join(c for c in pdf_text if c.isalnum() or c in (' ', '-', '_')).strip()
                    if not safe_pdf_name:
                        safe_pdf_name = f"document_{pdfs_downloaded + 1}"

                    # Remove .pdf or pdf extension if already present to avoid double extension
                    if safe_pdf_name.lower().endswith('.pdf'):
                        safe_pdf_name = safe_pdf_name[:-4].strip()
                    elif safe_pdf_name.lower().endswith('pdf'):
                        safe_pdf_name = safe_pdf_name[:-3].strip()

                    pdf_output_path = page_dir / f"{safe_pdf_name}.pdf"

                    download_tasks.append({
                        'pdf_url': pdf_url,
                        'output_path': str(pdf_output_path),
                        'course_id': str(course_id),
                        'pdf_text': pdf_text,
                    })

                # Download PDFs in parallel to speed up module scraping
                if download_tasks:
                    # Allow user to tune concurrency; cap at number of tasks and ensure at least 1
                    workers = max(1, min(max_workers or 1, len(download_tasks)))
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        future_to_task = {
                            executor.submit(
                                self.download_pdf_from_url,
                                task['pdf_url'],
                                task['output_path'],
                                task['course_id'],
                            ): task
                            for task in download_tasks
                        }

                        for future in as_completed(future_to_task):
                            task = future_to_task[future]
                            try:
                                success = future.result()
                            except Exception as e:
                                success = False
                                print(f"  ✗ Exception downloading '{task['pdf_text']}': {e}")

                            if success:
                                pdfs_downloaded += 1
                                print(f"  ✓ Downloaded: {task['pdf_text']}")
                            else:
                                print(f"  ✗ Failed to download: {task['pdf_text']}")
            else:
                print(f"  No PDFs found in page: {page_title}")
        
        print("-" * 50)
        if filters:
            print(f"✓ Downloaded {pdfs_downloaded} PDF(s) matching filters: {', '.join(filters)}")
        else:
            print(f"✓ Downloaded {pdfs_downloaded} PDF(s)")
    
    def list_module_documents(self, course_id: int, module_id: int):
        """List all documents/items in a module"""
        items = self.get_module_items(course_id, module_id)
        
        if not items:
            print("\nNo items found in this module.")
            return
        
        print(f"\n📄 Module Documents/Items:")
        print("-" * 50)
        
        for idx, item in enumerate(items, 1):
            item_title = item.get('title', 'Unknown Title')
            item_type = item.get('type', 'Unknown Type')
            item_id = item.get('id', 'N/A')
            print(f"{idx}. {item_title}")
            print(f"   Type: {item_type}")
            print(f"   ID: {item_id}\n")
        
        print("-" * 50)
        
        # Check if there are any page items
        page_items = [item for item in items if item.get('type') == 'Page']
        if page_items:
            download_choice = input(f"\nDownload {len(page_items)} page(s) and extract PDFs? (y/n): ").strip().lower()
            if download_choice == 'y':
                self.download_pages_and_pdfs_from_module(course_id, module_id)
    
    
    def list_courses(self):
        """Print available courses"""
        print("\n📖 Your Courses:")
        print("-" * 50)
        
        courses = self.get_courses()
        
        if not courses:
            print("No courses found or unable to fetch courses.")
            print("Check your Canvas URL and API token.")
            return
        
        for course in courses:
            course_id = course.get('id', 'N/A')
            course_name = course.get('name', 'Unknown Course')
            print(f"ID: {course_id:<6} | {course_name}")
        
        print("-" * 50)
    
    def select_course(self) -> Optional[int]:
        """Let user select a course interactively"""
        courses = self.get_courses()
        
        if not courses:
            print("No courses found.")
            return None
        
        print("\n📖 Select a Course:")
        print("-" * 50)
        
        for idx, course in enumerate(courses, 1):
            course_id = course.get('id', 'N/A')
            course_name = course.get('name', 'Unknown Course')
            print(f"{idx}. {course_name}")
        
        print("-" * 50)
        
        while True:
            try:
                choice = int(input(f"Enter course number (1-{len(courses)}): "))
                if 1 <= choice <= len(courses):
                    selected_course = courses[choice - 1]
                    return selected_course.get('id')
                else:
                    print(f"Please enter a number between 1 and {len(courses)}")
            except ValueError:
                print("Invalid input. Please enter a number.")


def main():
    """Main function"""
    
    # Configuration
    CANVAS_URL = "https://canvas.instructure.com"  # Replace with your Canvas instance
    API_TOKEN = os.getenv('CANVAS_API_TOKEN', "")  # Set via environment variable
    
    if not API_TOKEN:
        print("❌ Canvas API token not found!")
        print("\nHow to get your token:")
        print("1. Log into Canvas")
        print("2. Click your avatar → Account → Settings")
        print("3. Find 'Approved Integrations' → '+ New Access Token'")
        print("4. Copy the token and set it:")
        print("   export CANVAS_API_TOKEN='your_token_here'")
        print("\nOr update CANVAS_URL and API_TOKEN directly in this script")
        return
    
    # Initialize scraper
    scraper = CanvasScraper(CANVAS_URL, API_TOKEN)
    
    # Show menu
    print("\n🎓 Canvas Scraper Menu:")
    print("-" * 50)
    print("1. List all courses")
    print("2. View module fields")
    print("-" * 50)
    
    while True:
        choice = input("Enter your choice (1-2): ").strip()
        
        if choice == '1':
            scraper.list_courses()
            break
        
        elif choice == '2':
            course_id = scraper.select_course()
            if course_id:
                # Get modules and let user select one
                modules = scraper.get_course_modules(course_id)
                if not modules:
                    print("No modules found in this course.")
                    break
                
                print("\n📋 Select a Module:")
                print("-" * 50)
                for idx, module in enumerate(modules, 1):
                    print(f"{idx}. {module.get('name', 'Unknown Module')}")
                print("-" * 50)
                
                while True:
                    try:
                        module_choice = int(input(f"Enter module number (1-{len(modules)}): "))
                        if 1 <= module_choice <= len(modules):
                            selected_module = modules[module_choice - 1]
                            scraper.print_module_fields(course_id, selected_module['id'])
                            scraper.list_module_documents(course_id, selected_module['id'])
                            break
                        else:
                            print(f"Please enter a number between 1 and {len(modules)}")
                    except ValueError:
                        print("Invalid input. Please enter a number.")
            break
        
        else:
            print("Invalid choice. Please enter 1 or 2.")


if __name__ == "__main__":
    main()
