import time
import os
import re
import sys
import requests
import json
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import random

# Add random delays between actions
time.sleep(random.uniform(2, 5))

class UdemyTranscriptExtractor:
    def __init__(self, headless=False, summarize=False, api_key=None):
        """Initialize the Udemy transcript extractor."""
        self.options = Options()
        if headless:
            self.options.add_argument("--headless")

        self.options.add_argument("--no-sandbox")
        self.options.add_argument("--disable-dev-shm-usage")
        self.options.add_argument("--disable-notifications")
        self.options.add_argument("--window-size=1920,1080")

        # Avoid detection as automated browser
        self.options.add_argument("--disable-blink-features=AutomationControlled")
        self.options.add_experimental_option("excludeSwitches", ["enable-automation"])
        self.options.add_experimental_option("useAutomationExtension", False)

        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=self.options)

        # Execute CDP commands to prevent detection
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
            """
        })

        self.wait = WebDriverWait(self.driver, 30)
        self.processed_urls = set()  # Track processed URLs
        self.processed_lectures = set()  # Also track by lecture title
        self.summarize = summarize
        self.api_key = api_key

    def wait_for_manual_login(self, url):
        """Navigate to URL and wait for manual login process and CAPTCHA solving."""
        print(f"Navigating to: {url}")
        self.driver.get(url)

        print("\n==================================================")
        print("PLEASE LOGIN MANUALLY IN THE BROWSER WINDOW")
        print("==================================================")
        print("IMPORTANT: If you see a Cloudflare security check or CAPTCHA:")
        print("1. Complete the security check/CAPTCHA challenge")
        print("2. Then login to Udemy")
        print("3. Navigate to the first video of the course")
        print("The script will wait for you to complete these steps.")
        print("After you've completed all steps, press Enter to continue...")
        input()
        print("Continuing with transcript extraction...")

    def wait_for_cloudflare_to_clear(self):
        """Wait until Cloudflare check is completed"""
        max_wait = 60  # seconds
        for _ in range(max_wait):
            if "challenge" in self.driver.current_url or "cloudflare" in self.driver.current_url:
                time.sleep(1)
            else:
                return True
        return False

    def extract_all_transcripts(self, course_url, max_videos=0):
        """Extract transcripts from all videos in sequence with improved tracking."""
        try:
            # Wait for manual login first
            self.wait_for_manual_login(course_url)
            time.sleep(5)  # Allow page to load fully

            print("Opening transcript panel for the first time...")
            if self.find_and_enable_transcript():
                print("Successfully opened transcript panel")
            else:
                print("Could not open transcript panel. Please open it manually and press Enter to continue...")
                input()

            # Try to get course title or prompt for it if not found
            course_title = self.get_course_title()
            if course_title == f"udemy_course_{int(time.time())}":
                print("\nCouldn't detect course title automatically.")
                manual_course = input("Please enter the course title manually: ").strip()
                if manual_course:
                    course_title = self.sanitize_filename(manual_course)
                    print(f"Using manual course title: {course_title}")

            print(f"Course title: {course_title}")

            # Create output directories
            output_dir = os.path.join("udemy_transcripts", course_title)
            os.makedirs(output_dir, exist_ok=True)

            if self.summarize:
                summary_dir = os.path.join(output_dir, "summaries")
                os.makedirs(summary_dir, exist_ok=True)

            video_count = 0

            while max_videos == 0 or video_count < max_videos:
                current_url = self.driver.current_url
                print(f"Current URL: {current_url}")

                # Get lecture information with enhanced detection
                lecture_info = self.get_detailed_lecture_info()
                full_title = lecture_info["full_title"]

                # Ensure we have a valid title
                if not full_title or full_title.strip() == "":
                    print("Failed to get a valid lecture title. Please provide one manually:")
                    manual_number = input("Enter lecture number (e.g. '88'): ").strip()
                    manual_title = input("Enter lecture title (e.g. 'Replication'): ").strip()

                    if manual_number and manual_title:
                        full_title = f"{manual_number}. {manual_title}"
                        print(f"Using manual title: {full_title}")
                    else:
                        # Use URL component as last resort
                        lecture_id = re.search(r'/lecture/(\d+)', current_url).group(1) if re.search(r'/lecture/(\d+)',
                                                                                                     current_url) else str(
                            int(time.time()))
                        full_title = f"Lecture_{lecture_id}"
                        print(f"Using fallback title: {full_title}")

                # Use the full title format for files
                formatted_title = full_title

                if formatted_title in self.processed_lectures:
                    print(f"Already processed lecture: {formatted_title}. Trying to move to next video...")
                    if not self.navigate_to_next_video():
                        print("No more videos to process. Exiting.")
                        break
                    time.sleep(3)
                    continue

                print(f"\n[{video_count + 1}] Processing video: {formatted_title}")

                transcript_text = self.extract_transcript_text()

                if transcript_text:
                    safe_title = self.sanitize_filename(formatted_title)
                    filename = f"{safe_title}.txt"
                    filepath = os.path.join(output_dir, filename)

                    with open(filepath, 'w', encoding='utf-8') as f:
                        f.write("\n".join(transcript_text))

                    print(f"Transcript saved to: {filepath}")

                    if self.summarize and self.api_key:
                        try:
                            print(f"Generating summary for: {formatted_title}")
                            summary = self.generate_notion_friendly_summary(
                                "\n".join(transcript_text),
                                formatted_title,  # Pass the full lecture title with number
                                lecture_info.get("number", "")
                            )

                            if summary:
                                # Use the same naming scheme for summary files
                                summary_filename = f"{safe_title}_summary.md"
                                summary_filepath = os.path.join(summary_dir, summary_filename)

                                with open(summary_filepath, 'w', encoding='utf-8') as f:
                                    f.write(summary)

                                print(f"Summary saved to: {summary_filepath}")
                            else:
                                print(f"Failed to generate summary for: {formatted_title}")
                        except Exception as e:
                            print(f"Error generating summary: {str(e)}")

                    self.processed_lectures.add(formatted_title)
                    self.processed_urls.add(current_url)
                    video_count += 1
                else:
                    print(f"No transcript found for {formatted_title}")

                if not self.navigate_to_next_video():
                    print("No more videos to process. Exiting.")
                    break

                time.sleep(3)

            print(f"\nCompleted processing {video_count} videos.")
            return True

        except Exception as e:
            print(f"Error extracting transcripts: {str(e)}")
            import traceback
            traceback.print_exc()
            self.driver.save_screenshot("error_screenshot.png")
            print("Error screenshot saved as error_screenshot.png")
            return False

    def generate_notion_friendly_summary(self, transcript_text, lecture_title, lecture_number):
        """Generate a Notion-friendly summary of the transcript using GPT-4."""
        prompt = f"""Create a visually appealing, well-structured summary of this lecture transcript that will look great in Notion. The lecture title is: {lecture_title}.

    Follow these specific formatting guidelines for Notion:

    1. Start with a large H1 header showing the exact lecture title: {lecture_title} 
    2. Create a clear table of contents with H2 headers for main sections 
    3. Use proper Markdown formatting that Notion supports:
       - H1, H2, H3 headers for hierarchy (use # syntax)
       - Bold text using **double asterisks** for important concepts
       - Create clean bullet points and numbered lists where appropriate
       - Use `code blocks` for any technical terms, commands, or syntax
       - Create toggle lists for detailed explanations (use the > format)
       - Use proper block quotes for important quotations (use > for this)
       - Add horizontal dividers (---) between major sections
       - Use emojis to highlight key areas (📌, 🔑, ⚠️, 💡, etc.)

    4. Structure the content as follows:
       - Brief overview (2-3 sentences)
       - Key concepts with clear explanations
       - Important definitions highlighted
       - Step-by-step processes where applicable
       - Visual hierarchy that makes the summary scannable
       - A "Key Takeaways" section at the end

    5. Make the summary visually engaging with:
       - Consistent formatting
       - Strategic use of whitespace
       - Font variations (bold, italic) to guide the eye
       - Emoji icons (sparingly) as visual markers

    Create this summary specifically to look outstanding when imported into Notion. Prioritize clarity, visual structure, and professional appearance.

    Transcript:
    """

        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

        data = {
            "model": "gpt-4o-mini",  # Changed from "gpt-4" to "gpt-4o-mini"
            "messages": [
                {"role": "system",
                 "content": "You are an expert educational content specialist with deep expertise in knowledge synthesis, information architecture, and technical communication. Your specialty is transforming complex educational content into beautifully structured, comprehensive summaries optimized for Notion. You excel at identifying core concepts, establishing clear hierarchical relationships between ideas, highlighting key terminology with proper definitions, and creating visually engaging layouts that enhance learning retention. You incorporate learning psychology principles by including memorable examples, analogies, and visual cues throughout your summaries. For technical content, you ensure precise explanations of processes and concepts. You maintain academic rigor while making complex topics accessible, and you're skilled at creating summaries that serve as both quick reference materials and comprehensive study guides."},
                {"role": "user", "content": prompt + transcript_text}
            ],
            "temperature": 0.7,
            "max_tokens": 2500
        }

        try:
            response = requests.post(url, headers=headers, data=json.dumps(data))
            response.raise_for_status()

            result = response.json()
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            else:
                print("Unexpected API response format")
                return None
        except requests.exceptions.RequestException as e:
            print(f"API request failed: {str(e)}")
            if hasattr(e, 'response') and e.response:
                print(f"Response status: {e.response.status_code}")
                print(f"Response body: {e.response.text}")
            return None

    def sanitize_filename(self, filename):
        """Sanitize a string to make it suitable as a filename."""
        # Remove invalid filename characters
        sanitized = re.sub(r'[\\/*?:"<>|]', "", filename)
        # Replace spaces and other problematic characters
        sanitized = re.sub(r'[\s-]+', '_', sanitized)
        # Ensure the filename isn't too long
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        return sanitized

    def navigate_to_next_video(self):
        """Click the 'Next' button to navigate to the next video."""
        try:
            print("Looking for 'Next' button...")

            # Save the current URL to check if navigation was successful
            current_url = self.driver.current_url

            # Try multiple approaches to find the next button
            found_and_clicked = False

            # Approach 1: Using specific selectors for the next button
            next_button_selectors = [
                "div[data-purpose='go-to-next']",
                "#go-to-next-item",
                ".next-and-previous--next--8Avih",
                ".next-and-previous--button---fNLz.next-and-previous--next--8Avih",
                "button.ud-btn.ud-btn-medium.ud-btn-primary[aria-label*='next']",
                "button.ud-btn.ud-btn-medium.ud-btn-primary[aria-describedby*='popper-content']"
            ]

            for selector in next_button_selectors:
                try:
                    next_buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if next_buttons:
                        for button in next_buttons:
                            try:
                                if button.is_displayed():
                                    # Scroll to the button to make sure it's in view
                                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});",
                                                               button)
                                    time.sleep(0.5)

                                    # Click using JavaScript for more reliable clicking
                                    self.driver.execute_script("arguments[0].click();", button)
                                    print(f"Successfully clicked 'Next' button with selector: {selector}")
                                    found_and_clicked = True
                                    break
                            except:
                                continue
                        if found_and_clicked:
                            break
                except Exception as e:
                    print(f"Error with selector {selector}: {str(e)}")

            # Approach 2: Try using XPath to find Next button based on text or SVG icon
            if not found_and_clicked:
                try:
                    # Look for elements containing "Next" text or with Next icon
                    next_xpath_selectors = [
                        "//div[contains(@class, 'next') and @data-purpose='go-to-next']",
                        "//div[contains(@class, 'next')]//svg",
                        "//div[@id='go-to-next-item']",
                        "//svg[@aria-label='Go to Next lecture']",
                        "//button[contains(text(), 'Next')]",
                        "//div[contains(text(), 'Next')]"
                    ]

                    for xpath in next_xpath_selectors:
                        elements = self.driver.find_elements(By.XPATH, xpath)
                        if elements:
                            for elem in elements:
                                try:
                                    if elem.is_displayed():
                                        # Scroll and click
                                        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});",
                                                                   elem)
                                        time.sleep(0.5)
                                        self.driver.execute_script("arguments[0].click();", elem)
                                        print(f"Successfully clicked element with XPath: {xpath}")
                                        found_and_clicked = True
                                        break
                                except:
                                    continue
                            if found_and_clicked:
                                break
                except Exception as e:
                    print(f"XPath approach failed: {str(e)}")

            # Approach 3: Last resort - look for any clickable element that might be the next button
            if not found_and_clicked:
                try:
                    # Take a screenshot for debugging
                    self.driver.save_screenshot("next_button_debug.png")
                    print("Screenshot saved as next_button_debug.png")

                    print("\nCould not find 'Next' button automatically. You have options:")
                    print("1. Manually navigate to the next video and continue")
                    print("2. Stop extraction")
                    choice = input("Enter choice (1 or 2): ")

                    if choice == "1":
                        print("Please navigate to the next video in the browser window.")
                        print("After navigating, press Enter to continue...")
                        input()
                        found_and_clicked = True
                    else:
                        return False
                except:
                    return False

            # Check if navigation was successful by waiting for URL change
            # Wait up to 10 seconds for the URL to change
            max_wait = 10
            wait_time = 0
            while wait_time < max_wait:
                if self.driver.current_url != current_url:
                    print("Successfully navigated to next video")
                    # Wait to make sure the page loads properly
                    time.sleep(2)
                    return True
                time.sleep(1)
                wait_time += 1

            if found_and_clicked:
                print("Button was clicked, but URL didn't change. Continuing anyway...")
                return True

            print("Navigation to next video failed")
            return False

        except Exception as e:
            print(f"Error navigating to next video: {str(e)}")
            return False

    def find_and_enable_transcript(self):
        """Try multiple methods to find and enable transcript button."""
        # List of possible transcript toggle selectors, including SVG button
        toggle_selectors = [
            # SVG transcript icon
            "svg[aria-label='Transcript in sidebar region']",
            "svg[aria-label*='Transcript']",
            "[aria-label*='Transcript']",
            # Button selectors
            "button[data-purpose='transcript-toggle']",
            "button.transcript--transcript-button--3TvKV",
            "button[aria-label='Transcript']",
            ".captions-display--captions-cta-container--FbsFM button",
            "[data-purpose='captions-toggle-button']"
        ]

        for selector in toggle_selectors:
            try:
                print(f"Looking for transcript toggle with selector: {selector}")
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)

                if elements:
                    print(f"Found {len(elements)} potential transcript buttons, clicking the first one...")
                    # Try to click using JavaScript for more reliable clicking
                    self.driver.execute_script("arguments[0].click();", elements[0])
                    time.sleep(2)
                    return True
            except Exception as e:
                print(f"Selector {selector} not found or couldn't be clicked. Error: {str(e)}")

        # Special case - look for any element containing "transcript" in text or attributes
        try:
            # Get all elements on the page
            all_elements = self.driver.find_elements(By.XPATH,
                                                     "//*[contains(text(), 'transcript') or contains(translate(@*, 'TRANSCRIPT', 'transcript'), 'transcript')]")
            if all_elements:
                print(f"Found {len(all_elements)} elements containing 'transcript'. Trying to click them...")
                for elem in all_elements[:5]:  # Try first 5 to avoid clicking too many
                    try:
                        self.driver.execute_script("arguments[0].click();", elem)
                        time.sleep(1)
                        print("Clicked an element containing 'transcript'")
                    except:
                        pass
        except:
            pass

        # Check if transcript is already visible
        try:
            transcript_containers = [
                ".transcript--transcript-panel--1EX49",
                ".transcript--cue-container--Vuwj6",
                ".captions-display--captions-container--PqdGQ",
                "[data-purpose='transcript-cue']"
            ]

            for container in transcript_containers:
                if self.driver.find_elements(By.CSS_SELECTOR, container):
                    print(f"Transcript panel appears to be already visible (found {container}).")
                    return True
        except:
            pass

        print("Could not find or enable transcript panel. Taking a screenshot for debugging...")
        self.driver.save_screenshot("transcript_button_debug.png")
        print("Screenshot saved as transcript_button_debug.png")
        print("\nPlease check the screenshot to see the current state of the page.")
        print("If you can see the transcript button, you can try to click it manually.")
        print("After clicking the transcript button manually, press Enter to continue...")
        input()
        return True

    def extract_transcript_text(self):
        """Try multiple methods to extract transcript text."""
        # Array of text content
        transcript_text = []

        # Methods to try for extracting transcript content
        methods = [
            # Method 1: Original transcript container spans
            {
                "selector": "div.transcript--cue-container--Vuwj6 p[data-purpose='transcript-cue'] span[data-purpose='cue-text']",
                "attribute": "text"
            },
            # Method 2: Captions container divs
            {
                "selector": "div.captions-display--captions-container--PqdGQ div",
                "attribute": "text"
            },
            # Method 3: Generic transcript cues
            {
                "selector": "[data-purpose='transcript-cue'] span",
                "attribute": "text"
            },
            # Method 4: Direct transcript container
            {
                "selector": ".transcript--transcript-panel--1EX49 p",
                "attribute": "text"
            },
            # Method 5: HTML content method for captions
            {
                "selector": "div.captions-display--captions-container--PqdGQ",
                "attribute": "innerHTML"
            },
            # Method 6: Any element with 'transcript-cue' in its attributes
            {
                "selector": "[class*='transcript-cue']",
                "attribute": "text"
            },
            # Method 7: Any transcript container
            {
                "selector": "[class*='transcript']",
                "attribute": "innerHTML"
            }
        ]

        for method in methods:
            try:
                print(f"Trying to extract transcript with selector: {method['selector']}")

                if method["attribute"] == "innerHTML":
                    # Special case for innerHTML method
                    elements = self.driver.find_elements(By.CSS_SELECTOR, method["selector"])
                    if elements:
                        html_content = elements[0].get_attribute("innerHTML")
                        # Parse the HTML to extract text
                        soup = BeautifulSoup(html_content, 'html.parser')
                        texts = [text.strip() for text in soup.stripped_strings if text.strip()]
                        if texts:
                            transcript_text = texts
                else:
                    # Standard text extraction
                    elements = self.driver.find_elements(By.CSS_SELECTOR, method["selector"])

                    for element in elements:
                        text = element.text.strip()
                        if text:
                            transcript_text.append(text)

                if transcript_text:
                    print(f"Successfully extracted {len(transcript_text)} transcript segments.")
                    return transcript_text

            except Exception as e:
                print(f"Method failed: {str(e)}")

        # If we get here, try one last desperate method - get all visible text from potential transcript areas
        try:
            print("Attempting last-resort transcript extraction method...")
            # Try to find any element that might contain transcript text
            potential_containers = self.driver.find_elements(By.XPATH,
                                                             "//*[contains(@class, 'transcript') or contains(@class, 'captions')]")

            if potential_containers:
                for container in potential_containers:
                    # Get all text from the container
                    all_text = container.text
                    # Split by lines and filter empty lines
                    lines = [line.strip() for line in all_text.split('\n') if line.strip()]
                    if lines:
                        print(f"Last-resort method found {len(lines)} lines of text.")
                        return lines
        except Exception as e:
            print(f"Last-resort method failed: {str(e)}")

        print("All automated methods failed. You can try to manually copy the transcript.")
        print("If you can see the transcript on the page, press Enter to take a screenshot")
        print("and then try to extract the text from the screenshot...")
        input()
        self.driver.save_screenshot("transcript_content_debug.png")
        print("Screenshot saved as transcript_content_debug.png")

        return []

    def get_detailed_lecture_info(self):
        """Get detailed lecture info including title, section, and lecture number with improved targeting."""
        lecture_info = {
            "title": "",
            "section": "",
            "number": "",
            "full_title": ""
        }

        # Method 1: Look for the active/current lecture element specifically
        try:
            # First, look for elements with indicators of being the current lecture
            active_indicators = [
                ".curriculum-item-link--active--NshF4",
                "[aria-current='true']",
                ".curriculum-item-link--is-current--2mKk4",
                ".item-link--active"
            ]

            for indicator in active_indicators:
                active_elements = self.driver.find_elements(By.CSS_SELECTOR, indicator)
                if active_elements:
                    for active_elem in active_elements:
                        try:
                            # Find the title element within this active lecture container
                            title_elem = active_elem.find_element(By.CSS_SELECTOR, "[data-purpose='item-title']")
                            if title_elem:
                                title_text = title_elem.text.strip()
                                if title_text:
                                    print(f"Found active lecture title: '{title_text}'")

                                    # Extract lecture number from the title (e.g. "88. Replication")
                                    lecture_num_match = re.match(r'^(\d+)\.\s+(.*)', title_text)
                                    if lecture_num_match:
                                        lecture_info["number"] = lecture_num_match.group(1)
                                        lecture_info["title"] = lecture_num_match.group(2)
                                        lecture_info["full_title"] = title_text
                                    else:
                                        # Look for the number separately within the active element
                                        number_elem = active_elem.find_element(By.CSS_SELECTOR,
                                                                               ".curriculum-item-link--item-number--3PmJf")
                                        if number_elem:
                                            lecture_info["number"] = number_elem.text.strip().rstrip('.')
                                            lecture_info["title"] = title_text
                                            lecture_info["full_title"] = f"{lecture_info['number']}. {title_text}"
                                        else:
                                            lecture_info["title"] = title_text
                                            lecture_info["full_title"] = title_text

                                    # If we found valid info, return it immediately
                                    if lecture_info["full_title"]:
                                        return lecture_info
                        except:
                            continue
        except Exception as e:
            print(f"Active element approach failed: {str(e)}")

        # Method 2: Look for title in the currently playing video area
        try:
            video_title_selectors = [
                ".video-viewer--title-overlay--OoQ6p",
                ".video-viewer--title--Jk6xW",
                "[data-purpose='video-title']",
                ".ud-heading-xl.clp-lead__title",
                ".course-overview--title--2-V0B"
            ]

            for selector in video_title_selectors:
                title_elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                for elem in title_elements:
                    if elem.is_displayed():
                        title_text = elem.text.strip()
                        if title_text:
                            print(f"Found title from video player: '{title_text}'")

                            # Try to find lecture number in various ways
                            lecture_num_match = re.match(r'^(\d+)\.\s+(.*)', title_text)
                            if lecture_num_match:
                                lecture_info["number"] = lecture_num_match.group(1)
                                lecture_info["title"] = lecture_num_match.group(2)
                                lecture_info["full_title"] = title_text
                            else:
                                # Check URL for lecture number
                                current_url = self.driver.current_url
                                url_num_match = re.search(r'/lecture/(\d+)', current_url)
                                if url_num_match:
                                    lecture_info["number"] = url_num_match.group(1)
                                    lecture_info["title"] = title_text
                                    lecture_info["full_title"] = f"{lecture_info['number']}. {title_text}"
                                else:
                                    lecture_info["title"] = title_text
                                    lecture_info["full_title"] = title_text

                            # If we found valid info, return it immediately
                            if lecture_info["full_title"]:
                                return lecture_info
        except Exception as e:
            print(f"Video player title approach failed: {str(e)}")

        # Method 3: Use JavaScript to get the current lecture information directly
        try:
            js_script = """
            // Find the active lecture element
            const activeLecture = document.querySelector('.curriculum-item-link--active--NshF4, [aria-current="true"], .curriculum-item-link--is-current--2mKk4');

            if (activeLecture) {
                // Look for the title within the active lecture
                const titleElem = activeLecture.querySelector('[data-purpose="item-title"]');
                return titleElem ? titleElem.textContent.trim() : '';
            } else {
                // Fall back to video title if no active lecture is found
                const videoTitle = document.querySelector('.video-viewer--title-overlay--OoQ6p, .video-viewer--title--Jk6xW, [data-purpose="video-title"]');
                return videoTitle ? videoTitle.textContent.trim() : '';
            }
            """

            js_title = self.driver.execute_script(js_script)
            if js_title:
                print(f"Found title via JavaScript: '{js_title}'")

                # Extract lecture number from the title
                lecture_num_match = re.match(r'^(\d+)\.\s+(.*)', js_title)
                if lecture_num_match:
                    lecture_info["number"] = lecture_num_match.group(1)
                    lecture_info["title"] = lecture_num_match.group(2)
                    lecture_info["full_title"] = js_title
                else:
                    # Use URL for lecture number
                    current_url = self.driver.current_url
                    url_num_match = re.search(r'/lecture/(\d+)', current_url)
                    if url_num_match:
                        lecture_info["number"] = url_num_match.group(1)
                        lecture_info["title"] = js_title
                        lecture_info["full_title"] = f"{lecture_info['number']}. {js_title}"
                    else:
                        lecture_info["title"] = js_title
                        lecture_info["full_title"] = js_title
        except Exception as e:
            print(f"JavaScript extraction failed: {str(e)}")

        # Method 4: Use URL and current visible page elements as a fallback
        if not lecture_info["full_title"]:
            try:
                current_url = self.driver.current_url
                print(f"Analyzing URL for lecture info: {current_url}")

                # Get lecture ID from URL
                lecture_match = re.search(r'/lecture/(\d+)', current_url)
                if lecture_match:
                    lecture_id = lecture_match.group(1)
                    lecture_info["number"] = lecture_id

                    # Check page title as a fallback
                    page_title = self.driver.title
                    if page_title:
                        # Remove common suffixes like "| Udemy"
                        clean_title = re.sub(r'\s*\|.*$', '', page_title).strip()
                        lecture_info["title"] = clean_title
                        lecture_info["full_title"] = f"{lecture_id}. {clean_title}"
                    else:
                        # Last resort - use lecture ID
                        lecture_info["title"] = f"Lecture_{lecture_id}"
                        lecture_info["full_title"] = f"{lecture_id}. Lecture_{lecture_id}"
            except Exception as e:
                print(f"URL analysis failed: {str(e)}")

        # If all methods fail, prompt for manual input
        if not lecture_info["full_title"]:
            print("\nCouldn't detect lecture title automatically.")
            manual_number = input("Enter lecture number (e.g. '3'): ").strip()
            manual_title = input("Enter lecture title (e.g. 'Provisioning a Snowflake Trial Account'): ").strip()

            if manual_number and manual_title:
                lecture_info["number"] = manual_number
                lecture_info["title"] = manual_title
                lecture_info["full_title"] = f"{manual_number}. {manual_title}"
                print(f"Using manual input: '{lecture_info['full_title']}'")
            else:
                # Generate a timestamp as absolute last resort
                ts = int(time.time()) % 1000
                lecture_info["number"] = str(ts)
                lecture_info["title"] = f"Lecture_{ts}"
                lecture_info["full_title"] = f"{lecture_info['number']}. {lecture_info['title']}"

        print(
            f"Final lecture info: number='{lecture_info['number']}', title='{lecture_info['title']}', full_title='{lecture_info['full_title']}'")
        return lecture_info

    def get_lecture_info(self):
        """Get both lecture title and section info using the correct selectors."""
        lecture_info = {
            "title": "",
            "section": ""
        }

        # First, try to get the lecture title using the data-purpose="item-title" element
        try:
            # Use the exact selector from the provided HTML
            title_selectors = [
                "span[data-purpose='item-title']",  # From the HTML you provided
                ".curriculum-item-link--curriculum-item-title-content--S-urg span[data-purpose='item-title']",
                ".curriculum-item-link--curriculum-item-title-content--1wtO_ span",
                "span.truncate-with-tooltip--ellipsis--YJw4N span",
                # Additional backup selectors
                ".ud-focus-visible-target .curriculum-item-link--curriculum-item-title--VBsdR span"
            ]

            for selector in title_selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    for element in elements:
                        if element.is_displayed():
                            title = element.text.strip()
                            if title:
                                print(f"Found lecture title: '{title}' using selector: {selector}")
                                lecture_info["title"] = title
                                break
                    if lecture_info["title"]:
                        break
        except Exception as e:
            print(f"Error getting lecture title: {str(e)}")

        # Now try to get the section information
        try:
            section_selectors = [
                "span.ud-accordion-panel-heading",
                ".ud-heading-sm[data-purpose='section-title']",
                "[data-purpose='section-title']",
                ".ud-accordion-panel-toggler .ud-accordion-panel-title"
            ]

            for selector in section_selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    for element in elements:
                        if element.is_displayed():
                            section = element.text.strip()
                            if section:
                                print(f"Found section title: '{section}' using selector: {selector}")
                                # Extract section number if present
                                section_match = re.search(r'Section (\d+):', section)
                                if section_match:
                                    lecture_info["section"] = f"Section {section_match.group(1)}"
                                else:
                                    lecture_info["section"] = section
                                break
                    if lecture_info["section"]:
                        break
        except Exception as e:
            print(f"Error getting section info: {str(e)}")

        # If we couldn't find the title using the selectors, try more aggressive methods
        if not lecture_info["title"]:
            try:
                # Try looking for any visible element that might contain the lecture title
                title_candidates = self.driver.find_elements(By.XPATH,
                                                             "//div[contains(@class, 'curriculum-item')]//span[contains(@class, 'truncate') or contains(@class, 'title')]")

                for element in title_candidates:
                    if element.is_displayed():
                        title = element.text.strip()
                        if title and len(title) < 100:  # Reasonable title length
                            print(f"Found potential lecture title (fallback method): '{title}'")
                            lecture_info["title"] = title
                            break
            except Exception as e:
                print(f"Error with fallback title method: {str(e)}")

        # Last resort if we still don't have a title
        if not lecture_info["title"]:
            try:
                url = self.driver.current_url
                # Extract lecture ID from URL
                lecture_match = re.search(r'/lecture/(\d+)', url)
                if lecture_match:
                    lecture_id = lecture_match.group(1)
                    lecture_info["title"] = f"lecture_{lecture_id}"
                else:
                    # Generate a timestamp-based title as last resort
                    lecture_info["title"] = f"lecture_{int(time.time())}"
            except:
                lecture_info["title"] = f"lecture_{int(time.time())}"

        return lecture_info

    def get_course_title(self):
        """Get the course title using multiple selectors and JavaScript."""
        # Try using JavaScript first
        try:
            course_title_js = """
            var titleElement = document.querySelector('a[data-purpose="course-title-link"], .course-title--course-title--3r1sL, .ud-heading-xl, [data-purpose="course-header-title"], h1');
            return titleElement ? titleElement.textContent.trim() : '';
            """
            course_title = self.driver.execute_script(course_title_js)

            if course_title:
                print(f"Found course title via JavaScript: {course_title}")
                return self.sanitize_filename(course_title)
        except Exception as e:
            print(f"JavaScript course title extraction failed: {str(e)}")

        # Fallback to traditional selectors
        selectors = [
            "a[data-purpose='course-title-link']",
            ".course-title--course-title--3r1sL",
            ".ud-heading-xl",
            "[data-purpose='course-header-title']",
            "h1"
        ]

        for selector in selectors:
            try:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    for elem in elements:
                        if elem.is_displayed():
                            title = elem.text.strip()
                            if title:
                                print(f"Found course title with selector {selector}: {title}")
                                return self.sanitize_filename(title)
            except:
                pass

        # Look for title in page title
        try:
            page_title = self.driver.title
            if page_title:
                # Remove common suffixes like "| Udemy"
                title = re.sub(r'\s*\|.*$', '', page_title).strip()
                print(f"Using page title: {title}")
                return self.sanitize_filename(title)
        except:
            pass

        # Final fallback - use timestamp
        return f"udemy_course_{int(time.time())}"

    def close(self):
        """Close the browser."""
        self.driver.quit()
        print("Browser closed.")
        print('-----------------------')
        print("From Houssini With Love")
        print('-----------------------')





def validate_api_key(api_key):
    """Validate the OpenAI API key by making a simple test request."""
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    data = {
        "model": "gpt-4o-mini",  # Changed from "gpt-4" to "gpt-4o-mini"
        "messages": [
            {"role": "user", "content": "Hello"}
        ],
        "max_tokens": 5
    }

    try:
        response = requests.post(url, headers=headers, data=json.dumps(data))
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"API key validation failed: {str(e)}")
        if hasattr(e, 'response') and e.response:
            print(f"Response status: {e.response.status_code}")
            print(f"Response body: {e.response.text}")
        return False


def main():
    """Main function to handle user input and control the transcript extraction."""
    # Check if URL was provided as command line argument
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        # Prompt for URL if not provided as argument
        url = input("Enter Udemy course URL: ")

    # Ask user how many videos to process with clear instructions
    try:
        user_input = input("Enter the maximum number of videos to process (enter 0 for all videos): ")
        if user_input.strip() == "0" or user_input.strip() == "":
            max_videos = 0
            print("Will process all available videos.")
        else:
            max_videos = int(user_input)
            print(f"Will process up to {max_videos} videos.")
    except ValueError:
        print("Invalid input, defaulting to process all videos.")
        max_videos = 0

    # Ask if the user wants to generate summaries
    summarize_input = input("Do you want to generate summaries for the lectures using GPT-4? (y/n): ").lower()
    summarize = summarize_input == 'y' or summarize_input == 'yes'

    api_key = None
    if summarize:
        api_key = input("Please provide your OpenAI API key: ").strip()
        print("Validating API key...")
        if not validate_api_key(api_key):
            print("Invalid API key or API connection failed. Proceeding without summarization.")
            summarize = False
            api_key = None
        else:
            print("API key validated successfully.")

    # Initialize the extractor
    headless = "--headless" in sys.argv
    if headless:
        print("Warning: Headless mode is not recommended when manual login is required.")
        print("Continue with headless mode anyway? (y/n)")
        if input().lower() != 'y':
            headless = False

    extractor = UdemyTranscriptExtractor(headless=headless, summarize=summarize, api_key=api_key)

    try:
        # Extract transcripts from all videos in sequence
        extractor.extract_all_transcripts(url, max_videos=max_videos)
    finally:
        # Close the browser
        extractor.close()


if __name__ == "__main__":
    main()