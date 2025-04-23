import streamlit as st
import os
import time
import base64
import zipfile
import io
import threading
import queue
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
# Assuming this module exists and is compatible - may need to be adapted too
from ibm_udemy_transcript_scraper import UdemyTranscriptExtractor, validate_api_key


def create_zip_file(files_data):
    """Create a zip file from memory data instead of from disk"""
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path, file_content in files_data.items():
            zipf.writestr(file_path, file_content)
    memory_file.seek(0)
    return memory_file


def get_download_link(file_content, filename, text):
    """Generate a download link for the file"""
    b64 = base64.b64encode(file_content.getvalue()).decode()
    href = f'<a href="data:application/zip;base64,{b64}" download="{filename}" class="download-button">{text}</a>'
    return href


def init_cloud_browser():
    """Initialize a browser compatible with Streamlit Cloud"""
    options = Options()
    
    # Required for headless browser in cloud environment
    options.add_argument("--headless=new")  # Using newer headless mode
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")  # Larger window size for better visibility
    
    # Anti-detection settings
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    # Realistic user agent
    options.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    try:
        # First try: Use ChromeDriverManager with specific version
        try:
            driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
            return driver
        except Exception as e:
            print(f"First attempt failed: {str(e)}")
            
        # Second try: Use direct Chrome binary path (common in cloud environments)
        try:
            options.binary_location = "/usr/bin/google-chrome"  # Common path in cloud environments
            driver = webdriver.Chrome(options=options)
            return driver
        except Exception as e:
            print(f"Second attempt failed: {str(e)}")
            
        # Third try: Use Playwright as fallback
        try:
            from playwright.sync_api import sync_playwright
            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            return page
        except Exception as e:
            print(f"Third attempt failed: {str(e)}")
            
        raise Exception("All browser initialization attempts failed")
        
    except Exception as e:
        print(f"Browser initialization error: {str(e)}")
        raise Exception(f"Failed to initialize browser: {str(e)}")


def init_visible_browser():
    """Initialize a visible browser for debugging and manual interaction"""
    options = Options()
    
    # Basic settings for visible browser
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-extensions")
    
    # Anti-detection settings
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    # Realistic user agent
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    except Exception as e:
        st.error(f"Failed to initialize visible Chrome: {str(e)}")
        raise Exception("Failed to initialize visible browser")
    
    return driver


def handle_login(driver, course_url, udemy_email, udemy_password, status_queue):
    """Handle the Udemy login process specifically selecting the second login option"""
    try:
        # Navigate to course URL first
        driver.get(course_url)
        status_queue.put(("status", "Navigated to course page. Looking for login elements..."))
        time.sleep(3)
        
        # Look for login button or element
        try:
            login_btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable(
                    (By.XPATH, "//a[contains(@class, 'login') or contains(@data-purpose, 'header-login')]"))
            )
            login_btn.click()
            status_queue.put(("status", "Clicked on login button."))
            time.sleep(2)
        except Exception as e:
            status_queue.put(("status", f"Login button not found, might already be on login page: {str(e)}"))
            
        # Find and click on the second login option
        try:
            # Wait for login options to be visible
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'auth-method')]"))
            )
            
            # Get all login options
            login_options = driver.find_elements(By.XPATH, "//div[contains(@class, 'auth-method')] | //form[contains(@class, 'login-form')] | //button[contains(@class, 'auth-button')]")
            
            if len(login_options) > 1:
                # Click the second option
                login_options[1].click()
                status_queue.put(("status", "Selected second login option."))
            else:
                # If we can't find auth method containers, try finding individual login buttons
                login_buttons = driver.find_elements(By.XPATH, "//button[contains(@class, 'auth') or contains(text(), 'Log') or contains(text(), 'Sign')]")
                if len(login_buttons) > 1:
                    login_buttons[1].click()
                    status_queue.put(("status", "Selected second login button."))
                else:
                    status_queue.put(("status", "Could not find multiple login options. Proceeding with available login form."))
            
            time.sleep(2)
        except Exception as e:
            status_queue.put(("status", f"Error selecting second login option: {str(e)}. Proceeding with available login form."))
        
        # Find and fill the email/username field
        try:
            # Try several possible field selectors
            selectors = [
                (By.NAME, "email"),
                (By.ID, "email"),
                (By.NAME, "username"),
                (By.ID, "username"),
                (By.ID, "user"),
                (By.XPATH, "//input[@type='email']"),
                (By.XPATH, "//input[@placeholder='Email' or @placeholder='Username' or @placeholder='Email or username']"),
                (By.XPATH, "//input[contains(@class, 'email') or contains(@class, 'username')]")
            ]
            
            email_field = None
            for selector_type, selector_value in selectors:
                try:
                    email_field = WebDriverWait(driver, 3).until(
                        EC.presence_of_element_located((selector_type, selector_value))
                    )
                    if email_field:
                        break
                except:
                    continue
            
            if email_field:
                email_field.clear()
                email_field.send_keys(udemy_email)
                status_queue.put(("status", "Entered email/username."))
            else:
                status_queue.put(("status", "Could not find email/username field."))
                return False
        except Exception as e:
            status_queue.put(("status", f"Error entering email/username: {str(e)}"))
            return False
        
        # Find and fill the password field
        try:
            # Try several possible field selectors for password
            password_selectors = [
                (By.NAME, "password"),
                (By.ID, "password"),
                (By.XPATH, "//input[@type='password']"),
                (By.XPATH, "//input[@placeholder='Password']"),
                (By.XPATH, "//input[contains(@class, 'password')]")
            ]
            
            password_field = None
            for selector_type, selector_value in password_selectors:
                try:
                    password_field = WebDriverWait(driver, 3).until(
                        EC.presence_of_element_located((selector_type, selector_value))
                    )
                    if password_field:
                        break
                except:
                    continue
            
            if password_field:
                password_field.clear()
                password_field.send_keys(udemy_password)
                status_queue.put(("status", "Entered password."))
            else:
                status_queue.put(("status", "Could not find password field."))
                return False
        except Exception as e:
            status_queue.put(("status", f"Error entering password: {str(e)}"))
            return False
        
        # Find and click the submit button
        try:
            # Try several possible button selectors
            button_selectors = [
                (By.XPATH, "//button[@type='submit']"),
                (By.XPATH, "//button[contains(text(), 'Log in') or contains(text(), 'Sign in') or contains(text(), 'Login')]"),
                (By.XPATH, "//input[@type='submit']"),
                (By.XPATH, "//button[contains(@class, 'login') or contains(@class, 'submit')]")
            ]
            
            submit_button = None
            for selector_type, selector_value in button_selectors:
                try:
                    submit_button = WebDriverWait(driver, 3).until(
                        EC.element_to_be_clickable((selector_type, selector_value))
                    )
                    if submit_button:
                        break
                except:
                    continue
            
            if submit_button:
                submit_button.click()
                status_queue.put(("status", "Clicked submit button. Waiting for login to complete..."))
                time.sleep(8)  # Give enough time for login to complete
            else:
                status_queue.put(("status", "Could not find submit button."))
                return False
        except Exception as e:
            status_queue.put(("status", f"Error clicking submit button: {str(e)}"))
            return False
        
        # Check if login was successful by looking for user profile elements or course content
        try:
            # Wait for either course content or profile elements to be present
            WebDriverWait(driver, 10).until(
                EC.any_of(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'course-content')]")),
                    EC.presence_of_element_located((By.XPATH, "//a[contains(@class, 'user-profile')]")),
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'sidebar')]"))
                )
            )
            status_queue.put(("status", "Login successful! Detected course elements."))
            
            # Navigate back to course URL to ensure we're on the right page
            driver.get(course_url)
            status_queue.put(("status", "Navigated back to course page after login."))
            time.sleep(5)
            
            return True
        except Exception as e:
            status_queue.put(("status", f"Could not verify successful login: {str(e)}"))
            return False
            
    except Exception as e:
        status_queue.put(("status", f"Login process failed: {str(e)}"))
        return False


def navigate_to_first_lecture(driver, status_queue):
    """Navigate to the first lecture of the course"""
    try:
        # Try to find and click "Start Course" or "Continue" button
        button_selectors = [
            (By.XPATH, "//button[contains(text(), 'Start') or contains(@data-purpose, 'start-course')]"),
            (By.XPATH, "//a[contains(text(), 'Start') or contains(@data-purpose, 'start-course')]"),
            (By.XPATH, "//button[contains(text(), 'Continue') or contains(@data-purpose, 'continue-course')]"),
            (By.XPATH, "//a[contains(text(), 'Continue') or contains(@data-purpose, 'continue-course')]"),
            (By.XPATH, "//button[contains(@class, 'start') or contains(@class, 'course-cta')]"),
            (By.XPATH, "//a[contains(@class, 'start') or contains(@class, 'course-cta')]")
        ]
        
        start_button = None
        for selector_type, selector_value in button_selectors:
            try:
                start_button = WebDriverWait(driver, 3).until(
                    EC.element_to_be_clickable((selector_type, selector_value))
                )
                if start_button:
                    break
            except:
                continue
        
        if start_button:
            start_button.click()
            status_queue.put(("status", "Clicked on start/continue course button."))
            time.sleep(5)
            return True
        
        # If no button found, try to find and click on the first lecture directly
        lecture_selectors = [
            (By.XPATH, "//a[contains(@class, 'lecture') and contains(@class, 'item')]"),
            (By.XPATH, "//div[contains(@class, 'lecture-item')]//a"),
            (By.XPATH, "//div[contains(@class, 'curriculum-item')]//a"),
            (By.XPATH, "//li[contains(@class, 'curriculum-item')]//a")
        ]
        
        first_lecture = None
        for selector_type, selector_value in lecture_selectors:
            try:
                lectures = driver.find_elements(selector_type, selector_value)
                if lectures and len(lectures) > 0:
                    first_lecture = lectures[0]
                    break
            except:
                continue
        
        if first_lecture:
            first_lecture.click()
            status_queue.put(("status", "Clicked on first lecture directly."))
            time.sleep(5)
            return True
        
        status_queue.put(("status", "Could not find navigation elements to first lecture. May already be in lecture view."))
        
        # Check if we're already in a lecture view
        try:
            # Look for typical lecture page elements
            WebDriverWait(driver, 5).until(
                EC.any_of(
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'video-player')]")),
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'lecture-view')]")),
                    EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'curriculum-navigation')]"))
                )
            )
            status_queue.put(("status", "Already in lecture view."))
            return True
        except:
            status_queue.put(("status", "Not in lecture view and couldn't navigate to first lecture."))
            return False
            
    except Exception as e:
        status_queue.put(("status", f"Error navigating to first lecture: {str(e)}"))
        return False


def modified_extract_all_transcripts(extractor, course_url, max_videos, status_queue):
    """A modified version of extract_all_transcripts that stores data in memory rather than files"""
    try:
        # Store the initial URL
        initial_url = extractor.driver.current_url

        status_queue.put(("status", "Finding and enabling transcript panel..."))
        if extractor.find_and_enable_transcript():
            status_queue.put(("status", "Successfully opened transcript panel"))
        else:
            status_queue.put(("status", "Could not open transcript panel automatically. Please open it manually."))
            time.sleep(10)

        # Get course title
        course_title = extractor.get_course_title()
        if not course_title or course_title == f"udemy_course_{int(time.time())}":
            status_queue.put(("status", "Couldn't detect course title automatically. Using default title."))
            course_title = "udemy_course_" + str(int(time.time()))

        status_queue.put(("status", f"Course title: {course_title}"))

        video_count = 0
        transcripts = []  # Store transcripts in memory

        while max_videos == 0 or video_count < max_videos:
            current_url = extractor.driver.current_url
            status_queue.put(("status", f"Processing video at URL: {current_url}"))

            # Get lecture information
            lecture_info = extractor.get_detailed_lecture_info()
            full_title = lecture_info["full_title"]

            if not full_title or full_title.strip() == "":
                status_queue.put(("status", "Failed to get a valid lecture title. Using fallback title."))
                lecture_id = extractor.driver.current_url.split("/")[-1]
                full_title = f"Lecture_{lecture_id}"

            formatted_title = full_title

            if formatted_title in extractor.processed_lectures:
                status_queue.put(("status", f"Already processed lecture: {formatted_title}. Moving to next video..."))
                if not extractor.navigate_to_next_video():
                    status_queue.put(("status", "No more videos to process. Extraction complete."))
                    break
                time.sleep(3)
                continue

            status_queue.put(("progress", {
                "current": video_count + 1,
                "max": max_videos if max_videos > 0 else "unknown",
                "title": formatted_title
            }))

            transcript_text = extractor.extract_transcript_text()

            if transcript_text:
                safe_title = extractor.sanitize_filename(formatted_title)
                transcript_content = "\n".join(transcript_text)

                # Store transcript in memory
                transcripts.append({
                    'title': safe_title,
                    'content': transcript_content,
                    'lecture_info': lecture_info
                })

                status_queue.put(("status", f"✅ Successfully extracted: {formatted_title}"))

                if extractor.api_key:
                    try:
                        status_queue.put(("status", f"Generating high-end notes for: {formatted_title}"))
                        summary = extractor.generate_notion_friendly_summary(
                            transcript_content,
                            formatted_title,
                            lecture_info.get("number", "")
                        )

                        if summary:
                            # Store summary in memory
                            transcripts[-1]['summary'] = summary
                            status_queue.put(("status", f"✅ Successfully summarized: {formatted_title}"))
                        else:
                            status_queue.put(("status", f"❌ Failed to generate notes for: {formatted_title}"))
                    except Exception as e:
                        status_queue.put(("status", f"❌ Error generating notes: {str(e)}"))

                extractor.processed_lectures.add(formatted_title)
                extractor.processed_urls.add(current_url)
                video_count += 1
            else:
                status_queue.put(("status", f"❌ No transcript found for {formatted_title}"))

            if max_videos > 0 and video_count >= max_videos:
                status_queue.put(("status", f"✅ Completed processing {video_count} videos as requested."))
                break

            if max_videos == 0 or video_count < max_videos:
                if not extractor.navigate_to_next_video():
                    status_queue.put(("status", "No more videos to process. Extraction complete."))
                    break
                time.sleep(3)

        status_queue.put(("status", f"✅ Completed processing {video_count} videos."))
        return course_title, True, transcripts

    except Exception as e:
        status_queue.put(("status", f"❌ Error during extraction: {str(e)}"))
        import traceback
        traceback.print_exc()
        status_queue.put(("status", f"Error details: {str(e)}"))
        return None, False, []


def handle_ibm_login(driver, course_url, ibm_email, ibm_password, status_queue):
    """Handle the IBM w3id login process for Udemy for Business"""
    try:
        # Check if we're using Playwright
        is_playwright = hasattr(driver, 'goto')
        
        # Navigate to course URL first
        if is_playwright:
            driver.goto(course_url)
        else:
            driver.get(course_url)
        status_queue.put(("status", "Navigated to course page. Looking for login elements..."))
        time.sleep(3)
        
        # Look for login button or element
        try:
            if is_playwright:
                # Playwright selectors
                login_btn = driver.wait_for_selector("a[class*='login'], a[data-purpose*='header-login']", timeout=10000)
                login_btn.click()
            else:
                # Selenium selectors
                login_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//a[contains(@class, 'login') or contains(@data-purpose, 'header-login')]"))
                )
                login_btn.click()
            status_queue.put(("status", "Clicked on login button."))
            time.sleep(2)
        except Exception as e:
            status_queue.put(("status", f"Login button not found, might already be on login page: {str(e)}"))
        
        # Handle the IBM Security Verify screen - select "w3id Credentials" option
        try:
            if is_playwright:
                # Wait for the w3id Credentials button
                w3id_button = driver.wait_for_selector("#credsDiv", timeout=15000)
                w3id_button.click()
            else:
                # Wait for the w3id Credentials button
                w3id_button = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.ID, "credsDiv"))
                )
                w3id_button.click()
            
            status_queue.put(("status", "Selected w3id Credentials option"))
            time.sleep(5)
            
        except Exception as e:
            status_queue.put(("status", f"Error selecting w3id option: {str(e)}. Attempting to continue..."))
        
        # Now handle the username/password form
        try:
            if is_playwright:
                # Fill email field
                email_field = driver.wait_for_selector("#user-name-input", timeout=15000)
                email_field.fill(ibm_email)
                
                # Fill password field
                password_field = driver.wait_for_selector("#password-input", timeout=10000)
                password_field.fill(ibm_password)
                
                # Click sign in button
                submit_button = driver.wait_for_selector("#login-button", timeout=10000)
                submit_button.click()
            else:
                # Fill email field
                email_field = WebDriverWait(driver, 15).until(
                    EC.presence_of_element_located((By.ID, "user-name-input"))
                )
                email_field.clear()
                email_field.send_keys(ibm_email)
                
                # Fill password field
                password_field = WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.ID, "password-input"))
                )
                password_field.clear()
                password_field.send_keys(ibm_password)
                
                # Click sign in button
                submit_button = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.ID, "login-button"))
                )
                submit_button.click()
            
            status_queue.put(("status", "Submitted login credentials. Waiting for login to complete..."))
            time.sleep(10)  # Give enough time for login and potential redirects
            
        except Exception as e:
            status_queue.put(("status", f"Error with username/password form: {str(e)}"))
            return False
        
        # Check if login was successful
        try:
            if is_playwright:
                # Wait for course elements
                driver.wait_for_selector("div[class*='course-content'], a[class*='user-profile'], div[class*='sidebar']", timeout=15000)
            else:
                # Wait for course elements
                WebDriverWait(driver, 15).until(
                    EC.any_of(
                        EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'course-content')]")),
                        EC.presence_of_element_located((By.XPATH, "//a[contains(@class, 'user-profile')]")),
                        EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'sidebar')]"))
                    )
                )
            
            status_queue.put(("status", "IBM login successful! Detected course elements."))
            
            # Navigate back to course URL to ensure we're on the right page
            if is_playwright:
                driver.goto(course_url)
            else:
                driver.get(course_url)
            status_queue.put(("status", "Navigated back to course page after login."))
            time.sleep(5)
            
            return True
        except Exception as e:
            status_queue.put(("status", f"Could not verify successful IBM login: {str(e)}"))
            return False
            
    except Exception as e:
        status_queue.put(("status", f"IBM login process failed: {str(e)}"))
        return False


def extraction_thread(driver, course_url, max_videos, api_key, status_queue, ibm_email, ibm_password):
    """Run extraction in a separate thread with IBM login handling"""
    try:
        status_queue.put(("status", "Starting IBM w3id login process..."))
        
        # Check if we're using Playwright
        is_playwright = hasattr(driver, 'goto')  # Playwright page has goto method
        
        if is_playwright:
            # Handle Playwright navigation
            driver.goto(course_url)
            status_queue.put(("status", "Navigated to course page using Playwright"))
        else:
            # Handle Selenium navigation
            driver.get(course_url)
            status_queue.put(("status", "Navigated to course page using Selenium"))
        
        # Handle IBM login
        login_success = handle_ibm_login(driver, course_url, ibm_email, ibm_password, status_queue)
        
        if not login_success:
            status_queue.put(("error", "IBM login process failed. Please check your credentials and try again."))
            return
        
        status_queue.put(("status", "Login successful. Navigating to first lecture..."))
        
        # Navigate to first lecture
        navigation_success = navigate_to_first_lecture(driver, status_queue)
        
        if not navigation_success:
            status_queue.put(("error", "Failed to navigate to first lecture. Please check the course URL and try again."))
            return
        
        status_queue.put(("status", "Successfully navigated to first lecture. Initializing extractor..."))
        
        # Initialize extractor with the existing driver
        extractor = UdemyTranscriptExtractor(headless=True, summarize=True, api_key=api_key)
        extractor.driver = driver  # Use the pre-initialized driver

        status_queue.put(("status", "Extractor initialized. Beginning extraction process..."))

        # Call modified extraction function
        course_title, success, transcripts = modified_extract_all_transcripts(extractor, course_url, max_videos, status_queue)

        if success and transcripts:
            status_queue.put(("status", f"Successfully extracted {len(transcripts)} transcripts."))
            
            # Process data in memory
            files_data = prepare_files_data(course_title, transcripts)

            # Create zip file with memory data
            zip_file = create_zip_file(files_data)

            status_queue.put(("success", {
                "course_title": course_title,
                "transcripts": transcripts,
                "zip_file": zip_file,
                "files_data": files_data
            }))
        else:
            status_queue.put(("error", "Extraction failed. No transcripts were extracted."))
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        status_queue.put(("error", f"Error during extraction: {str(e)}\n\nError details:\n{error_details}"))
    finally:
        # Close the browser
        try:
            if is_playwright:
                driver.close()
                driver.context.browser.close()
            else:
                driver.quit()
            status_queue.put(("status", "Browser closed."))
        except Exception as e:
            status_queue.put(("status", f"Error closing browser: {str(e)}"))
        # Signal that the thread is done
        status_queue.put(("done", None))


def prepare_files_data(course_title, transcripts):
    """Prepare files data in memory instead of saving to disk"""
    files_data = {}

    for transcript in transcripts:
        # Store transcript
        transcript_file = f"{course_title}/{transcript['title']}.txt"
        files_data[transcript_file] = transcript['content']

        # Store summary if available
        if 'summary' in transcript:
            summary_file = f"{course_title}/summaries/{transcript['title']}_summary.md"
            files_data[summary_file] = transcript['summary']

    return files_data


def main():
    st.set_page_config(
        page_title="Udemy Course Summarization",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # Initialize session state variables
    if 'extraction_started' not in st.session_state:
        st.session_state.extraction_started = False
    if 'extraction_complete' not in st.session_state:
        st.session_state.extraction_complete = False
    if 'driver' not in st.session_state:
        st.session_state.driver = None
    if 'status_queue' not in st.session_state:
        st.session_state.status_queue = queue.Queue()
    if 'status_messages' not in st.session_state:
        st.session_state.status_messages = []
    if 'download_data' not in st.session_state:
        st.session_state.download_data = None
    if 'progress' not in st.session_state:
        st.session_state.progress = {"current": 0, "max": 0, "title": ""}
    if 'error_message' not in st.session_state:
        st.session_state.error_message = None

    # Add advanced settings in sidebar
    with st.sidebar:
        st.title("Advanced Settings")
        headless_mode = st.checkbox("Run in headless mode", value=True, 
                                    help="Uncheck to see the browser window (useful for debugging login issues)")
        manual_verification = st.checkbox("Enable manual verification", value=False,
                                        help="Allow manual interaction with the browser during login")

    # Add custom CSS to make the app look more professional
    st.markdown("""
    <style>
    .stApp {
        max-width: 1200px;
        margin: 0 auto;
    }
    .status-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #f8f9fa;
        height: 300px;
        overflow-y: auto;
        margin-bottom: 1rem;
        border: 1px solid #dee2e6;
    }
    .error-box {
        padding: 1rem;
        border-radius: 0.5rem;
        background-color: #fff3f3;
        border: 1px solid #ffcdd2;
        margin-bottom: 1rem;
    }
    .download-button {
        background-color: #4CAF50;
        border: none;
        color: white;
        padding: 15px 30px;
        text-align: center;
        text-decoration: none;
        display: inline-block;
        font-size: 16px;
        margin: 4px 2px;
        cursor: pointer;
        border-radius: 8px;
        box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1);
        transition: all 0.3s;
    }
    .download-button:hover {
        background-color: #45a049;
        box-shadow: 0 6px 12px rgba(0, 0, 0, 0.15);
        transform: translateY(-2px);
    }
    .progress-bar {
        height: 20px;
        border-radius: 10px;
        margin: 10px 0;
        overflow: hidden;
        box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.2);
    }
    .progress-bar-inner {
        height: 100%;
        background-color: #4CAF50;
        background-image: linear-gradient(45deg, rgba(255, 255, 255, 0.15) 25%, transparent 25%, transparent 50%, rgba(255, 255, 255, 0.15) 50%, rgba(255, 255, 255, 0.15) 75%, transparent 75%, transparent);
        background-size: 1rem 1rem;
        text-align: center;
        color: white;
        padding: 0 10px;
        line-height: 20px;
        font-size: 12px;
        animation: progress-bar-stripes 1s linear infinite;
    }
    @keyframes progress-bar-stripes {
        from {background-position: 1rem 0}
        to {background-position: 0 0}
    }
    .signature {
        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        font-weight: 600;
        font-size: 1.2rem;
        color: #3a86ff;
        text-align: center;
        margin: 15px 0;
        text-shadow: 1px 1px 2px rgba(0,0,0,0.1);
        letter-spacing: 0.5px;
    }
    .download-notes-section {
        font-size: 24px;
        display: flex;
        align-items: center;
        margin-bottom: 20px;
    }
    .download-notes-section img {
        margin-right: 10px;
    }
    .process-button {
        background-color: #3498db;
        color: white;
        border: none;
        border-radius: 8px;
        padding: 12px 24px;
        margin-top: 10px;
        font-weight: 600;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        transition: all 0.3s ease;
    }
    .process-button:hover {
        background-color: #2980b9;
        box-shadow: 0 6px 8px rgba(0, 0, 0, 0.15);
        transform: translateY(-2px);
    }
    .info-box {
        background-color: #e3f2fd;
        padding: 15px;
        border-radius: 5px;
        border-left: 5px solid #2196f3;
        margin-bottom: 20px;
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("📚 Udemy Course Summarization (Notes)")
    st.write("""
    This app transforms Udemy course content into high-quality, structured notes using OpenAI GPT4o-mini.
    """)

    # Add a notice about Streamlit Cloud compatibility
    st.markdown("""
    <div class="info-box">
    <strong>⚠️ Note:</strong> This application is optimized for Streamlit Cloud deployment. It runs in headless mode, so you'll need to provide your IBM w3id credentials. The app uses secure credential handling and doesn't store any login information.
    </div>
    """, unsafe_allow_html=True)

    with st.expander("ℹ️ How to use", expanded=True):
        st.markdown("""
        ### Step-by-Step Guide
        1. Enter the Udemy course URL
        2. Provide your IBM w3id credentials (securely handled)
        3. Choose how many videos to process (0 for all)
        4. Provide your OpenAI API key for AI-generated notes (required)
        5. Click "Start Process" to begin
        6. The app will handle login and navigate to the course
        7. Wait for the extraction to complete
        8. Download your notes when complete!
        """)

    col1, col2 = st.columns([2, 1])
    
    with col1:
        with st.form("extraction_form"):
            course_url = st.text_input("Udemy Course URL", placeholder="https://www.udemy.com/course/your-course-name/")
            
            # Update to IBM login fields
            ibm_email = st.text_input("IBM Email", placeholder="your-email@ibm.com")
            ibm_password = st.text_input("IBM Password", type="password")
            
            col_videos, col_api = st.columns(2)
            with col_videos:
                max_videos = st.number_input("Number of videos (0 for all)", min_value=0, value=0)
            with col_api:
                api_key = st.text_input("OpenAI API Key (required)", type="password")
                
            start_process = st.form_submit_button("🚀 Start Process", use_container_width=True)

    with col2:
        st.markdown("### Status")
        if not st.session_state.extraction_started:
            st.info("Enter course details and click 'Start Process'")
        elif st.session_state.extraction_started and not st.session_state.extraction_complete:
            st.info("Extraction in progress...")
        elif st.session_state.extraction_complete:
            if st.session_state.error_message:
                st.error(st.session_state.error_message)
            else:
                st.success("Extraction completed! Download your notes.")
                st.markdown('<div class="signature">From Houssini With Love</div>', unsafe_allow_html=True)

    # Process start button
    if start_process and course_url and ibm_email and ibm_password:
        if not api_key:
            st.error("OpenAI API Key is required for generating notes.")
        else:
            st.session_state.extraction_started = True
            st.session_state.extraction_complete = False
            st.session_state.error_message = None
            st.session_state.status_messages = ["Starting extraction process..."]
            st.rerun()

    # Start extraction process
    if st.session_state.extraction_started and not st.session_state.extraction_complete:
        # Display status messages
        st.markdown("### Progress")
        
        # Create a progress bar
        if st.session_state.progress["max"] and st.session_state.progress["max"] != "unknown":
            progress_pct = min(100, int(st.session_state.progress["current"] / st.session_state.progress["max"] * 100))
            progress_html = f"""
            <div class="progress-bar">
                <div class="progress-bar-inner" style="width: {progress_pct}%">
                    {st.session_state.progress["current"]}/{st.session_state.progress["max"]} ({progress_pct}%)
                </div>
            </div>
            """
            st.markdown(progress_html, unsafe_allow_html=True)
        elif st.session_state.progress["current"] > 0:
            st.write(f"Processed {st.session_state.progress['current']} videos")
            
        if st.session_state.progress["title"]:
            st.caption(f"Current: {st.session_state.progress['title']}")
        
        # Display status messages in a scrollable box
        st.markdown('<div class="status-box">', unsafe_allow_html=True)
        for msg in st.session_state.status_messages:
            st.write(msg)
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Start extraction thread if not already started
        if not hasattr(st.session_state, 'thread') or st.session_state.thread is None:
            # Initialize browser
            if not st.session_state.driver:
                try:
                    st.session_state.status_messages.append("Initializing browser...")
                    if headless_mode:
                        st.session_state.driver = init_cloud_browser()
                    else:
                        st.session_state.driver = init_visible_browser()
                    st.session_state.status_messages.append("Browser initialized successfully.")
                except Exception as e:
                    st.session_state.status_messages.append(f"Browser initialization failed: {str(e)}")
                    st.session_state.error_message = f"Failed to initialize browser: {str(e)}"
                    st.session_state.extraction_complete = True
                    st.rerun()

            # Start the extraction thread with IBM credentials
            st.session_state.thread = threading.Thread(
                target=extraction_thread,
                args=(st.session_state.driver, course_url, max_videos, api_key, st.session_state.status_queue, ibm_email, ibm_password)
            )
            st.session_state.thread.daemon = True
            st.session_state.thread.start()
        
        # Check for and process messages from the thread
        messages_processed = False
        while not st.session_state.status_queue.empty():
            message = st.session_state.status_queue.get_nowait()
            messages_processed = True
            
            if isinstance(message, tuple):
                msg_type, content = message
                
                if msg_type == "status":
                    st.session_state.status_messages.append(content)
                
                elif msg_type == "progress":
                    st.session_state.progress = content
                
                elif msg_type == "success":
                    st.session_state.extraction_complete = True
                    st.session_state.download_data = content
                    st.session_state.status_messages.append("✅ Notes generation completed successfully!")
                
                elif msg_type == "error":
                    st.session_state.error_message = content
                    st.session_state.extraction_complete = True
                    st.session_state.status_messages.append(f"❌ Error: {content}")
                
                elif msg_type == "done":
                    # Thread is done
                    st.session_state.thread = None
            
            else:  # Legacy message format
                st.session_state.status_messages.append(str(message))
        
        if messages_processed:
            st.rerun()
        else:
            # If no messages were processed, sleep and rerun
            time.sleep(1)
            st.rerun()
    
    # Show download section when extraction is complete
    if st.session_state.extraction_complete and st.session_state.download_data and not st.session_state.error_message:
        st.markdown("---")
        st.markdown('<div class="download-notes-section">📥 Download Your Notes</div>', unsafe_allow_html=True)
        
        col1, col2 = st.columns(2)
        
        with col1:
            course_title = st.session_state.download_data["course_title"]
            transcript_count = len(st.session_state.download_data["transcripts"])
            summary_count = sum(1 for t in st.session_state.download_data["transcripts"] if 'summary' in t)
            
            st.markdown(f"**Course**: {course_title}")
            st.markdown(f"**Processed Lectures**: {transcript_count}")
            st.markdown(f"**Generated Notes**: {summary_count}")
        
        with col2:
            # Display download button with improved styling
            zip_file = st.session_state.download_data["zip_file"]
            st.markdown(get_download_link(zip_file, f"{course_title}_notes.zip", "📥 Download Notes"),
                        unsafe_allow_html=True)
            
            # Option to restart the process with improved button
            if st.button("🔄 Process Another Course", type="primary", use_container_width=True,
                         key="process_another", help="Start over with a new course"):
                # Reset everything
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

if __name__ == "__main__":
    main()