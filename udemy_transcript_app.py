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
from ibm_udemy_transcript_scraper import UdemyTranscriptExtractor, validate_api_key


def create_zip_file(directory_path, files_to_include=None):
    """Create a zip file from specific files in a directory"""
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(directory_path):
            for file in files:
                file_path = os.path.join(root, file)
                # Only include specified files if a list is provided
                if files_to_include is None or file_path in files_to_include:
                    arcname = os.path.relpath(file_path, os.path.dirname(directory_path))
                    zipf.write(file_path, arcname=arcname)
    memory_file.seek(0)
    return memory_file


def get_download_link(file_content, filename, text):
    """Generate a download link for the file"""
    b64 = base64.b64encode(file_content.getvalue()).decode()
    href = f'<a href="data:application/zip;base64,{b64}" download="{filename}" class="download-button">{text}</a>'
    return href


def init_browser():
    """Initialize the browser with settings to help avoid Cloudflare detection"""
    options = Options()

    # Basic settings
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-notifications")
    options.add_argument("--window-size=1920,1080")

    # Anti-detection settings
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Mac ARM64 specific settings
    options.add_argument("--remote-debugging-port=9222")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-browser-side-navigation")
    options.add_argument("--disable-features=IsolateOrigins,site-per-process")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-web-security")

    # User agent
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    try:
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
    except Exception as e:
        st.error(f"First attempt failed: {str(e)}")
        try:
            options.binary_location = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            service = Service()
            driver = webdriver.Chrome(service=service, options=options)
        except Exception as e:
            st.error(f"Second attempt failed: {str(e)}")
            raise Exception("""
            Failed to initialize ChromeDriver. Please try the following steps:
            1. Make sure Chrome is installed and up to date
            2. Run: pip install --upgrade webdriver-manager
            3. Run: rm -rf ~/.wdm/drivers/chromedriver
            4. Restart your computer
            5. Try running the app again
            If the issue persists, please check your Chrome version and let us know.
            """)

    # Execute CDP commands to prevent detection
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        })
        """
    })

    # Add additional CDP commands to make the browser look more human-like
    driver.execute_cdp_cmd("Network.setUserAgentOverride", {
        "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    })

    return driver


def modified_extract_all_transcripts(extractor, course_url, max_videos, status_queue):
    """A modified version of extract_all_transcripts with better status updates"""
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
        if course_title == f"udemy_course_{int(time.time())}":
            status_queue.put(("status", "Couldn't detect course title automatically. Using default title."))
            course_title = "udemy_course_" + str(int(time.time()))

        status_queue.put(("status", f"Course title: {course_title}"))

        # Create output directories
        output_dir = os.path.join("udemy_transcripts", course_title)
        os.makedirs(output_dir, exist_ok=True)

        # Always create summary directory as it's now the main feature
        summary_dir = os.path.join(output_dir, "summaries")
        os.makedirs(summary_dir, exist_ok=True)

        video_count = 0
        transcripts = []  # Store transcripts in memory instead of saving to files
        processed_files = []  # Keep track of processed files for this session

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

                # Specific success message for extraction of current lecture
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
                            # Specific success message for summarization of current lecture
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
        extractor.driver.save_screenshot("error_screenshot.png")
        status_queue.put(("status", "Error screenshot saved as error_screenshot.png"))
        return None, False, []


def extraction_thread(driver, course_url, max_videos, api_key, status_queue):
    """Run extraction in a separate thread with streamlit-compatible approach"""
    try:
        status_queue.put(("status", "Initializing extractor..."))
        # Always enable summarization since it's the main feature now
        extractor = UdemyTranscriptExtractor(headless=False, summarize=True, api_key=api_key)
        extractor.driver = driver  # Use the pre-initialized driver

        status_queue.put(("status", "Beginning extraction process..."))

        # Call our modified extraction function
        course_title, success, transcripts = modified_extract_all_transcripts(extractor, course_url, max_videos,
                                                                              status_queue)

        if success:
            # Save transcripts to files and get list of created files
            output_dir, processed_files = save_transcripts(course_title, transcripts)
            # Create zip file with only the processed files from this session
            zip_file = create_zip_file(output_dir, processed_files)

            status_queue.put(("success", {
                "course_title": course_title,
                "transcripts": transcripts,
                "zip_file": zip_file,
                "output_dir": output_dir,
                "processed_files": processed_files
            }))
        else:
            status_queue.put(("error", "Extraction failed."))
    except Exception as e:
        status_queue.put(("error", f"Error: {str(e)}"))
    finally:
        # Close the browser
        try:
            driver.quit()
            status_queue.put(("status", "Browser closed."))
        except:
            pass
        # Signal that the thread is done
        status_queue.put(("done", None))


def save_transcripts(course_title, transcripts):
    """Save transcripts and summaries to files and return list of files created"""
    output_dir = os.path.join("udemy_transcripts", course_title)
    os.makedirs(output_dir, exist_ok=True)

    summary_dir = os.path.join(output_dir, "summaries")
    os.makedirs(summary_dir, exist_ok=True)

    processed_files = []  # Keep track of files created in this session

    for transcript in transcripts:
        # Save transcript
        transcript_file = os.path.join(output_dir, f"{transcript['title']}.txt")
        with open(transcript_file, 'w', encoding='utf-8') as f:
            f.write(transcript['content'])
        processed_files.append(transcript_file)

        # Save summary if available
        if 'summary' in transcript:
            summary_file = os.path.join(summary_dir, f"{transcript['title']}_summary.md")
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write(transcript['summary'])
            processed_files.append(summary_file)

    return output_dir, processed_files


def main():
    st.set_page_config(
        page_title="Udemy Course Summarization",
        page_icon="📚",
        layout="wide",
        initial_sidebar_state="expanded"
    )

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
    </style>
    """, unsafe_allow_html=True)

    st.title("📚 Udemy Course Summarization (Notes)")
    st.write("""
    This app transforms Udemy course content into high-quality, structured notes using OpenAI GPT4o-mini.
    """)

    # Initialize session state variables
    if 'browser_initialized' not in st.session_state:
        st.session_state.browser_initialized = False
    if 'extraction_started' not in st.session_state:
        st.session_state.extraction_started = False
    if 'extraction_complete' not in st.session_state:
        st.session_state.extraction_complete = False
    if 'driver' not in st.session_state:
        st.session_state.driver = None
    if 'ready_for_extraction' not in st.session_state:
        st.session_state.ready_for_extraction = False
    if 'cloudflare_verified' not in st.session_state:
        st.session_state.cloudflare_verified = False
    if 'status_queue' not in st.session_state:
        st.session_state.status_queue = queue.Queue()
    if 'status_messages' not in st.session_state:
        st.session_state.status_messages = []
    if 'download_data' not in st.session_state:
        st.session_state.download_data = None
    if 'progress' not in st.session_state:
        st.session_state.progress = {"current": 0, "max": 0, "title": ""}

    with st.expander("ℹ️ How to use", expanded=True):
        st.markdown("""
        ### Step-by-Step Guide
        1. Enter the Udemy course URL
        2. Choose how many videos to process (0 for all)
        3. Provide your OpenAI API key for AI-generated notes (required)
        4. Click "Start Process" to begin
        5. Log in to Udemy in the browser window that opens
        6. Navigate to the first lecture and solve any CAPTCHA/security checks
        7. Click "Ready to Extract" when you're on the first lecture
        8. The extraction will start automatically
        9. Download your notes when complete!
        """)

    col1, col2 = st.columns([2, 1])

    with col1:
        with st.form("extraction_form"):
            course_url = st.text_input("Udemy Course URL", placeholder="https://www.udemy.com/course/your-course-name/")

            col_videos, col_api = st.columns(2)
            with col_videos:
                max_videos = st.number_input("Number of videos (0 for all)", min_value=0, value=0)
            with col_api:
                api_key = st.text_input("OpenAI API Key (required)", type="password")

            start_process = st.form_submit_button("🚀 Start Process", use_container_width=True)

    with col2:
        st.markdown("### Status")
        if not st.session_state.browser_initialized:
            st.info("Enter course details and click 'Start Process'")
        elif not st.session_state.cloudflare_verified:
            st.warning("Please complete the Cloudflare verification in the browser")
        elif not st.session_state.ready_for_extraction:
            st.info("Navigate to the first lecture, then click 'Ready to Extract'")
        elif st.session_state.extraction_started and not st.session_state.extraction_complete:
            st.info("Extraction in progress...")
        elif st.session_state.extraction_complete:
            st.success("Extraction completed! Download your notes.")
            st.markdown('<div class="signature">From Houssini With Love</div>', unsafe_allow_html=True)

    # Process start button
    if start_process and course_url:
        if not api_key:
            st.error("OpenAI API Key is required for generating notes.")
        elif not st.session_state.browser_initialized:
            with st.spinner("Initializing browser..."):
                try:
                    st.session_state.driver = init_browser()
                    st.session_state.driver.get(course_url)
                    st.session_state.browser_initialized = True
                    st.session_state.status_messages = [
                        "Browser initialized. Please log in to Udemy and navigate to the first lecture."]
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to initialize browser: {str(e)}")

    # Check Cloudflare verification
    if st.session_state.browser_initialized and not st.session_state.cloudflare_verified:
        try:
            if "challenge" not in st.session_state.driver.current_url and "cloudflare" not in st.session_state.driver.current_url:
                st.session_state.cloudflare_verified = True
                st.session_state.status_messages.append(
                    "✅ Cloudflare verification completed. Please navigate to the first lecture.")
                st.rerun()
        except Exception as e:
            st.error(f"Error checking Cloudflare status: {str(e)}")

    # Ready to extract button
    if st.session_state.cloudflare_verified and not st.session_state.ready_for_extraction:
        ready_button = st.button("✅ Ready to Extract", use_container_width=True, type="primary")
        if ready_button:
            st.session_state.ready_for_extraction = True
            st.session_state.status_messages.append("Ready to begin extraction and notes generation.")
            st.session_state.extraction_started = True  # Auto-start extraction
            st.rerun()

    # Start extraction process
    if st.session_state.ready_for_extraction and st.session_state.extraction_started and not st.session_state.extraction_complete:
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
            st.session_state.thread = threading.Thread(
                target=extraction_thread,
                args=(st.session_state.driver, course_url, max_videos, api_key, st.session_state.status_queue)
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
                    st.session_state.status_messages.append(f"❌ Error: {content}")
                    st.session_state.extraction_complete = True  # End the process

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
    if st.session_state.extraction_complete and st.session_state.download_data:
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