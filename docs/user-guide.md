# Document Scraper - User Guide

This guide walks you through every feature of the Document Scraper application. No technical knowledge is required.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Scanning a Website](#scanning-a-website)
3. [Viewing Results](#viewing-results)
4. [Downloading Documents](#downloading-documents)
5. [Storing Documents in MongoDB](#storing-documents-in-mongodb)
6. [Browsing Stored Documents](#browsing-stored-documents)
7. [Scan History](#scan-history)
8. [Settings](#settings)
9. [Troubleshooting](#troubleshooting)

---

## Getting Started

Open the application in your web browser. You'll see the main scanning page with a URL input field, options for how to scan, and a "Start Scanning" button.

The navigation links at the top right let you access:
- **Documents** - Search and browse documents you've stored in MongoDB (see [Browsing Stored Documents](#browsing-stored-documents))
- **History** - View past scans
- **Settings** - Configure the application

---

## Scanning a Website

### Step 1: Enter a Website Address

Type or paste the website address you want to scan into the URL field. For example:

    https://example.com/documents

If you've scanned before, a dropdown will appear with your recent URLs so you can quickly re-scan a site.

You don't need to include `https://` - the application will add it automatically. If you enter something that doesn't look like a valid website address, a helpful message will appear explaining the issue.

### Step 2: Choose What to Look For

Use the **Document Type** dropdown to control which types of files the scanner looks for:

| Option | What it finds |
|---|---|
| **Common Documents** | PDFs, Word documents, Excel spreadsheets, PowerPoint presentations, text files, and CSVs |
| **All Files** | Everything above, plus images, audio/video files, archives (ZIP, RAR), and more |
| **PDFs Only** | Only PDF files |

### Step 3: Choose How Deep to Scan

You have two scanning modes:

**Single Page Only**
Scans just the page you entered. This is the fastest option and works well when all the documents are linked from a single page.

**Follow Internal Links**
The scanner will follow links within the website to find documents on other pages. You can control how many levels deep it goes using the depth control (the `-` and `+` buttons).

- **Depth 1** means it only follows links on the page you entered
- **Depth 2** means it also follows links found on those pages
- Higher depths scan more of the site but take longer

When using "Follow Internal Links", you'll also see a checkbox:

**"Scan entire site without pausing"**
- **Unchecked (default):** The scanner pauses after each batch of pages so you can review what it's found so far and decide whether to continue. This is recommended for large or unfamiliar sites.
- **Checked:** The scanner runs continuously until it has visited every reachable page. Use this when you know the site is a manageable size.

### Step 4: Start the Scan

Click **Start Scanning**. A progress window will appear showing:

- How many pages have been scanned
- How many documents have been found
- Estimated time remaining (when following links)

You can click **Cancel** at any time to stop the scan. Any documents found so far will still be available.

---

## Viewing Results

After a scan completes, you'll see the results page.

### Scan Summary

A summary window appears automatically showing key information about your scan:

- The URL you scanned
- How many pages were scanned and documents found
- How long the scan took
- Any errors that occurred

If there were errors (pages that couldn't be reached or documents that couldn't be accessed), you'll see a **Retry** button. Click it to try those items again - sometimes a temporary server issue resolves itself.

Click **"Show additional information"** to see the total size of all documents, plus the largest and smallest files found.

Click **View Documents** to close the summary and browse your results.

### Browsing Documents

Documents are displayed in one of two ways:

- **Card view** (50 or fewer documents): A visual grid showing each document as a card with its file type, name, and size.
- **Table view** (more than 50 documents): A compact list with columns for type, filename, size, and link depth. Use the page controls at the bottom to navigate through results.

Each document shows:
- A colored badge indicating the file type (red for PDF, blue for Word, green for Excel, etc.)
- The filename
- The file size (when available)
- A red warning if the file couldn't be accessed

### Searching and Filtering

Use the **search box** to find specific documents by name or type. Results update as you type.

The **"Hide inaccessible"** checkbox (on by default) hides any documents that couldn't be reached. Uncheck it to see everything the scanner found, including broken links.

### Selecting Documents

Click on any document card or table row to select it. You can also use the checkbox on each item. Selected documents are highlighted in blue.

Use the **Select All** and **Deselect All** buttons to quickly manage your selection. The counter shows how many documents you've selected.

### Continuing a Scan

If you used batch mode and there are more pages to scan, an amber banner will appear at the top: *"X more pages to scan"*.

You have two options:

- **Continue Scanning** - Scan the next batch of pages manually
- **Autoscan** - Automatically keep scanning until all pages are done. Click "Stop Autoscan" to pause at any time.

New documents found during continuation are added to your existing results. Your selections are preserved.

---

## Downloading Documents

### Choosing a Download Location

By default, documents download to your computer's file system. The **Download To** toggle at the bottom of the results page lets you switch between:

- **File System** - Saves files to a folder on your computer
- **MongoDB** - Stores files in a database with searchable text (see next section)

### File System Download

1. Enter the folder path where you want to save files. The default is `./downloads` (a "downloads" folder in the application directory).
2. The application checks the path as you type:
   - **Green message**: The folder exists and is ready
   - **Amber message**: The folder doesn't exist yet - click "Create it" to make it
   - **Red message**: There's a problem (not writable, not enough disk space, etc.)
3. Click the green **Download** button to start.

A progress window shows each file being downloaded, how many are complete, and how many have failed. You can cancel at any time.

---

## Storing Documents in MongoDB

MongoDB is an optional database that lets you store documents with searchable text content. This is useful if you want to search through the contents of documents later, or if you want AI-generated summaries.

### Setting Up MongoDB

1. Go to **Settings** (gear icon in the top right)
2. Scroll down to the **MongoDB Storage** section
3. Enter your MongoDB connection address (the default `mongodb://localhost:27017` works if MongoDB is installed on the same computer)
4. Click **Test Connection** to verify it works
5. Optionally change the database name (default: `document_scraper`)
6. Click **Save Settings**

If MongoDB isn't installed, the test will show an error message with a link to the installation guide.

### Downloading to MongoDB

1. On the results page, click **MongoDB** in the "Download To" toggle
2. The application will check the MongoDB connection and show a green or red status indicator
3. Select your documents and click **Store X files**
4. A progress window shows each file being downloaded and stored

When files are stored in MongoDB, the application automatically extracts the text content from supported file types (PDFs, Word documents, Excel spreadsheets, and text files). This extracted text makes the documents searchable later.

### AI Summaries

If you've configured an AI service in Settings, the application will automatically generate summaries of your documents after they're stored in MongoDB. This happens in the background - you'll see a blue note in the progress window saying *"AI summarization running in the background. You can close this dialog."*

The AI creates:
- A short summary of each document's contents
- Relevant keywords and tags
- A document type classification (report, invoice, manual, etc.)

To set up AI summarization:

1. Go to **Settings**
2. Scroll to the **AI Summarization** section
3. Enter the API URL, your API key, and the model name
4. Click **Save Settings**

---

## Browsing Stored Documents

Click **Documents** in the top navigation to search through everything you've stored in MongoDB across all past scans.

### Choosing a Scan

The **Scan** dropdown at the top lists every scan that has documents in MongoDB, along with how many documents each contains. Pick one to load its documents.

### Summarization Status

Once a scan is selected, a status bar appears showing AI-summary progress for that scan — e.g. *"42 of 60 summarized, 3 failed"*. Two action buttons can appear depending on state:

- **Summarize** - Runs AI summarization on any documents that don't yet have a summary. Hidden if everything is already summarized.
- **Retry Failed** - Resets the failure flag on any documents that previously errored and runs summarization on them again. Useful after fixing your AI key or switching models. Click the failure count to expand a panel showing the specific error message for each failed document.

If you haven't configured AI summarization in Settings, these buttons stay hidden.

### Searching

The search box matches against **filename, extracted text content, summary, short summary, keywords, and title** — anything that's been indexed for that document. Press Enter or click **Search** to run the query.

The **type filter** dropdown next to it limits results to a single file extension (PDF, DOCX, XLSX, etc.).

Results show the document title (from AI summary if available, otherwise the filename), a short summary, the keywords, and the source URL. Click any row to open the full document detail in a modal, where you can read the full summary and extracted text, copy the source link, or delete the document.

### Exporting

The **Export to CSV** button (next to the search button) downloads a CSV of every document in the currently-selected scan, including filename, source URL, file size, extracted-text length, summary, short summary, keywords, document type, and timestamps. Useful for sharing scan results outside the app.

---

## Scan History

Click **History** in the top navigation to see a record of all your past scans. The table shows:

| Column | What it means |
|---|---|
| **Date** | When the scan was performed (e.g., "Today 2:30 PM" or "Jan 15 10:30 AM") |
| **URL** | The website that was scanned |
| **Mode** | How the scan was done: Single, Batch, or Continuous |
| **Depth** | How many levels deep the scan went |
| **Pages** | How many pages were visited |
| **Docs** | How many documents were found |
| **Time** | How long the scan took |
| **Errors** | Number of errors encountered (red if any, gray if none) |
| **Size** | Total size of all documents found |

Click any row to see the full details of that scan in a popup window.

To clear all history, click the red **Clear History** button and confirm.

---

## Settings

Access settings by clicking the gear icon in the navigation bar. All changes require clicking **Save Settings** to take effect.

### Scan Performance

| Setting | What it controls | Default |
|---|---|---|
| **Pages Per Scan Batch** | How many pages to scan before pausing in batch mode | 500 |
| **Request Timeout** | How long to wait for a page to respond before giving up | 30 seconds |
| **Rate Limit** | How many requests per second to send. Lower values are gentler on the website being scanned | 2.0/second |
| **Concurrent Requests** | How many pages to scan at the same time. Higher is faster but uses more resources | 10 |

### Crawl Configuration

| Setting | What it controls | Default |
|---|---|---|
| **Default Crawl Depth** | The starting depth when "Follow Internal Links" is selected | 5 levels |
| **Maximum Crawl Depth** | The deepest you can set the crawl depth | 10 levels |
| **Scan History Limit** | How many past scans to keep in the history | 20 scans |

### MongoDB Storage

| Setting | What it controls | Default |
|---|---|---|
| **Connection URI** | The address of your MongoDB server | `mongodb://localhost:27017` |
| **Database Name** | Which database to store documents in | `document_scraper` |

Use the **Test Connection** button to verify your MongoDB setup.

### AI Summarization

| Setting | What it controls | Default |
|---|---|---|
| **API URL** | The address of the AI service | (empty) |
| **API Key** | Your authentication key for the AI service | (empty) |
| **Model** | Which AI model to use for summaries | (empty) |

All three fields must be filled in for AI summarization to work.

### Resetting Settings

Click **Reset to Defaults** to restore all settings to their original values. You'll be asked to confirm before any changes are made.

---

## Troubleshooting

### "MongoDB is not running"
MongoDB needs to be installed and started separately. Click the "Installation guide" link shown in the error message for instructions specific to your operating system.

### "MongoDB authentication failed"
The username or password in your connection URI is incorrect. Double-check the credentials in Settings.

### "Could not reach the MongoDB server"
The server address in your connection URI may be wrong, or the server may be on a different network. Check the URI in Settings.

### Documents show as "Not accessible"
The website may be blocking automated downloads, or the files may have been moved or deleted. Try the **Retry** button in the scan summary - sometimes temporary issues resolve themselves.

### OCR isn't working (image-only PDFs come back blank)
Image-only PDFs (scans, photos of pages) need OCR to extract text. The app uses [Stirling-PDF](https://github.com/Stirling-Tools/Stirling-PDF) running in a Docker container, and `scripts/run.sh` starts it for you. If OCR is silently producing empty text:

- Make sure Docker is installed and the daemon is running: `docker ps` should succeed.
- Check the container: `docker ps --filter name=stirling-pdf` should show it as `Up (healthy)`.
- On first run, the container can take 30–60 seconds to become ready; `scripts/run.sh` waits up to 180 seconds before warning.
- If something else is using port 8080, edit `STIRLING_PORT` in `scripts/run.sh`.
- View container logs with `docker logs stirling-pdf` for specific errors.

### "Browser error" while scanning JavaScript-heavy pages
The scanner falls back to a headless Chromium browser via Playwright for pages that load content with JavaScript. If you see "Browser error" messages in scan results, the Chromium binary may be missing. Activate the virtualenv and run:

```bash
playwright install chromium
```

### AI summaries are stuck at "pending" or never appear
- Open **Settings** and confirm the **API URL**, **API Key**, and **Model** are all filled in. All three are required.
- For OpenAI-compatible endpoints, your model name must match what your provider exposes (e.g. `gpt-5.4`, not `gpt-5`).
- If most documents went to "Failed" status, open the **Documents** page, select the scan, click the failure count to view the specific error, fix the cause (often an invalid key or model name), then click **Retry Failed**.
- Summarization runs in the background after a MongoDB download completes; you don't need to keep the progress dialog open.

### Scan seems stuck or slow
- Try reducing the **Concurrent Requests** setting if the target website is slow
- Try reducing the **Crawl Depth** if the site is very large
- Use **Single Page** mode if you only need documents from one specific page
- Check the **Rate Limit** setting - some websites block requests if you send too many too quickly

### URL validation errors

| Error message | What to do |
|---|---|
| "Please enter a website address" | The URL field is empty - type in a website address |
| "Please enter a valid website address" | Check for typos in the address |
| "Did you mean example.com?" | You may have entered just a word - add the domain ending (.com, .org, etc.) |
| "The website address contains spaces" | Remove any spaces from the URL |
| "The website address doesn't have a valid ending" | Make sure the address ends with something like .com, .org, .net, etc. |

### Download path errors

| Error message | What to do |
|---|---|
| "Directory does not exist" | Click "Create it" to make the folder, or type a different path |
| "Directory is not writable" | Choose a different folder that you have permission to write to |
| "Not enough disk space" | Free up space on your drive or choose a different location |
