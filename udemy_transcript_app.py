import streamlit as st
import os
import time
import base64
import zipfile
import io
import threading
import queue
from playwright.sync_api import sync_playwright
from ibm_udemy_transcript_scraper import UdemyTranscriptExtractor, validate_api_key


def create_zip_file(directory_path):
    """Create a zip file from the contents of a directory"""
    memory_file = io.BytesIO()
    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(directory_path):
            for file in files:
                file_path = os.path.join(root, file)
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
    """Initialize the browser with Playwright"""
    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--disable-gpu',
                '--window-size=1920,1080',
            ]
        )
        context = browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        return playwright, browser, context, page
    except Exception as e:
        st.error(f"Failed to initialize browser: {str(e)}")
        raise Exception("""
        Failed to initialize browser. Please try the following steps:
        1. Make sure you have the latest version of Playwright installed
        2. Run: pip install --upgrade playwright
        3. Run: playwright install chromium
        4. Try running the app again
        If the issue persists, please let us know.
        """)


def modified_extract_all_transcripts(extractor, course_url, max_videos, status_queue):
    """A modified version of extract_all_transcripts with better status updates"""
    try:
        # Store the initial URL
        initial_url = extractor.page.url

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

        if extractor.summarize:
            summary_dir = os.path.join(output_dir, "summaries")
            os.makedirs(summary_dir, exist_ok=True)

        video_count = 0
        transcripts = []  # Store transcripts in memory instead of saving to files

        while max_videos == 0 or video_count < max_videos:
            current_url = extractor.page.url
            status_queue.put(("status", f"Processing video at URL: {current_url}"))

            # Get lecture information
            lecture_info = extractor.get_detailed_lecture_info()
            full_title = lecture_info["full_title"]

            if not full_title or full_title.strip() == "":
                status_queue.put(("status", "Failed to get a valid lecture title. Using fallback title."))
                lecture_id = extractor.page.url.split("/")[-1]
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

                status_queue.put(("status", f"✅ Transcript extracted for: {formatted_title}"))

                if extractor.summarize and extractor.api_key:
                    try:
                        status_queue.put(("status", f"Generating summary for: {formatted_title}"))
                        summary = extractor.generate_notion_friendly_summary(
                            transcript_content,
                            formatted_title,
                            lecture_info.get("number", "")
                        )

                        if summary:
                            # Store summary in memory
                            transcripts[-1]['summary'] = summary
                            status_queue.put(("status", f"✅ Summary generated for: {formatted_title}"))
                        else:
                            status_queue.put(("status", f"❌ Failed to generate summary for: {formatted_title}"))
                    except Exception as e:
                        status_queue.put(("status", f"❌ Error generating summary: {str(e)}"))

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
        extractor.page.screenshot(path="error_screenshot.png")
        status_queue.put(("status", "Error screenshot saved as error_screenshot.png"))
        return None, False, []


def extraction_thread(page, course_url, max_videos, api_key, status_queue):
    """Run extraction in a separate thread with streamlit-compatible approach"""
    try:
        status_queue.put(("status", "Initializing extractor..."))
        extractor = UdemyTranscriptExtractor(headless=True, summarize=bool(api_key), api_key=api_key)
        extractor.page = page  # Use the Playwright page

        status_queue.put(("status", "Beginning extraction process..."))

        # Call our modified extraction function
        course_title, success, transcripts = modified_extract_all_transcripts(extractor, course_url, max_videos, status_queue)

        if success:
            # Save transcripts to files
            output_dir = save_transcripts(course_title, transcripts)
            zip_file = create_zip_file(output_dir)
            
            status_queue.put(("success", {
                "course_title": course_title,
                "transcripts": transcripts,
                "zip_file": zip_file,
                "output_dir": output_dir
            }))
        else:
            status_queue.put(("error", "Extraction failed."))
    except Exception as e:
        status_queue.put(("error", f"Error: {str(e)}"))
    finally:
        # Close the browser
        try:
            page.close()
            status_queue.put(("status", "Browser closed."))
        except:
            pass
        # Signal that the thread is done
        status_queue.put(("done", None))


def save_transcripts(course_title, transcripts):
    """Save transcripts and summaries to files"""
    output_dir = os.path.join("udemy_transcripts", course_title)
    os.makedirs(output_dir, exist_ok=True)
    
    summary_dir = os.path.join(output_dir, "summaries")
    os.makedirs(summary_dir, exist_ok=True)
    
    for transcript in transcripts:
        # Save transcript
        transcript_file = os.path.join(output_dir, f"{transcript['title']}.txt")
        with open(transcript_file, 'w', encoding='utf-8') as f:
            f.write(transcript['content'])
        
        # Save summary if available
        if 'summary' in transcript:
            summary_file = os.path.join(summary_dir, f"{transcript['title']}_summary.md")
            with open(summary_file, 'w', encoding='utf-8') as f:
                f.write(transcript['summary'])
    
    return output_dir


def main():
    st.set_page_config(
        page_title="Udemy Transcript Extractor",
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
        padding: 12px 24px;
        text-align: center;
        text-decoration: none;
        display: inline-block;
        font-size: 16px;
        margin: 4px 2px;
        cursor: pointer;
        border-radius: 4px;
    }
    .progress-bar {
        height: 20px;
        border-radius: 10px;
        margin: 10px 0;
        overflow: hidden;
    }
    .progress-bar-inner {
        height: 100%;
        background-color: #4CAF50;
        text-align: center;
        color: white;
        padding: 0 10px;
        line-height: 20px;
        font-size: 12px;
    }
    </style>
    """, unsafe_allow_html=True)

    st.title("📚 Udemy Transcript Extractor")
    st.write("""
    This app extracts transcripts from Udemy courses and optionally generates summaries using OpenAI GPT.
    """)

    # Initialize session state variables
    if 'browser_initialized' not in st.session_state:
        st.session_state.browser_initialized = False
    if 'extraction_started' not in st.session_state:
        st.session_state.extraction_started = False
    if 'extraction_complete' not in st.session_state:
        st.session_state.extraction_complete = False
    if 'status_queue' not in st.session_state:
        st.session_state.status_queue = queue.Queue()
    if 'status_messages' not in st.session_state:
        st.session_state.status_messages = []
    if 'download_data' not in st.session_state:
        st.session_state.download_data = None
    if 'progress' not in st.session_state:
        st.session_state.progress = {"current": 0, "max": 0, "title": ""}
    if 'playwright' not in st.session_state:
        st.session_state.playwright = None
    if 'browser' not in st.session_state:
        st.session_state.browser = None
    if 'context' not in st.session_state:
        st.session_state.context = None
    if 'page' not in st.session_state:
        st.session_state.page = None

    with st.expander("ℹ️ How to use", expanded=True):
        st.markdown("""
        ### Step-by-Step Guide
        1. Enter the Udemy course URL
        2. Choose how many videos to process (0 for all)
        3. Provide your OpenAI API key for AI-generated summaries (optional)
        4. Click "Start Process" to begin
        5. Log in to Udemy in the browser window that opens
        6. Navigate to the first lecture and solve any CAPTCHA/security checks
        7. Click "Ready to Extract" when you're on the first lecture
        8. The extraction will start automatically
        9. Download your transcripts when complete!
        """)

    col1, col2 = st.columns([2, 1])
    
    with col1:
        with st.form("extraction_form"):
            course_url = st.text_input("Udemy Course URL", placeholder="https://www.udemy.com/course/your-course-name/")
            
            col_videos, col_api = st.columns(2)
            with col_videos:
                max_videos = st.number_input("Number of videos (0 for all)", min_value=0, value=0)
            with col_api:
                api_key = st.text_input("OpenAI API Key (optional)", type="password")
                
            start_process = st.form_submit_button("🚀 Start Process")

    with col2:
        st.markdown("### Status")
        if not st.session_state.browser_initialized:
            st.info("Enter course details and click 'Start Process'")
        elif not st.session_state.extraction_complete:
            st.info("Extraction in progress...")
        elif st.session_state.extraction_complete:
            st.success("Extraction completed! Download your transcripts.")

    # Process start button
    if start_process and course_url:
        if not st.session_state.browser_initialized:
            with st.spinner("Initializing browser..."):
                try:
                    playwright, browser, context, page = init_browser()
                    st.session_state.playwright = playwright
                    st.session_state.browser = browser
                    st.session_state.context = context
                    st.session_state.page = page
                    st.session_state.page.goto(course_url)
                    st.session_state.browser_initialized = True
                    st.session_state.status_messages = ["Browser initialized. Please log in to Udemy and navigate to the first lecture."]
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to initialize browser: {str(e)}")
    
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
                args=(st.session_state.page, course_url, max_videos, api_key, st.session_state.status_queue)
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
                    st.session_state.status_messages.append("✅ Extraction completed successfully!")
                
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
        st.markdown("### 📥 Download Your Transcripts")
        
        col1, col2 = st.columns(2)
        
        with col1:
            course_title = st.session_state.download_data["course_title"]
            transcript_count = len(st.session_state.download_data["transcripts"])
            summary_count = sum(1 for t in st.session_state.download_data["transcripts"] if 'summary' in t)
            
            st.markdown(f"**Course**: {course_title}")
            st.markdown(f"**Transcripts**: {transcript_count}")
            st.markdown(f"**Summaries**: {summary_count}")
        
        with col2:
            # Display download button
            zip_file = st.session_state.download_data["zip_file"]
            st.markdown(get_download_link(zip_file, f"{course_title}_transcripts.zip", "📥 Download All Files"), unsafe_allow_html=True)
            
            # Option to restart the process
            if st.button("🔄 Extract Another Course"):
                # Reset everything
                for key in list(st.session_state.keys()):
                    del st.session_state[key]
                st.rerun()

    # Clean up on session end
    if st.session_state.browser_initialized and (st.session_state.extraction_complete or st.session_state.extraction_failed):
        try:
            if st.session_state.page:
                st.session_state.page.close()
            if st.session_state.context:
                st.session_state.context.close()
            if st.session_state.browser:
                st.session_state.browser.close()
            if st.session_state.playwright:
                st.session_state.playwright.stop()
        except:
            pass
        st.session_state.browser_initialized = False
        st.session_state.page = None
        st.session_state.context = None
        st.session_state.browser = None
        st.session_state.playwright = None


if __name__ == "__main__":
    main()