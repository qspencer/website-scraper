import asyncio
import aiohttp
import aiofiles
import os
from typing import AsyncGenerator, List, Optional

from app.core.config import settings
from app.core.logging_config import get_logger
from app.schemas.document import DocumentInfo, DownloadProgress
from app.utils.file_utils import (
    ensure_directory_exists,
    get_unique_filename,
    sanitize_filename,
)
from app.utils.url_utils import get_filename_from_url

logger = get_logger(__name__)


class DownloadService:
    def __init__(self):
        self.timeout = aiohttp.ClientTimeout(total=300)  # 5 minutes per file
        self.headers = {
            "User-Agent": settings.USER_AGENT,
        }
        self.chunk_size = 8192  # 8KB chunks

    async def download_file(
        self,
        session: aiohttp.ClientSession,
        url: str,
        destination_dir: str,
        filename: Optional[str] = None,
    ) -> tuple[bool, str, Optional[str]]:
        """
        Download a single file.

        Returns:
            Tuple of (success, message, saved_filename)
        """
        if not filename:
            filename = get_filename_from_url(url)

        filename = sanitize_filename(filename)
        filename = get_unique_filename(destination_dir, filename)
        filepath = os.path.join(destination_dir, filename)

        logger.info(f"Downloading: {url} -> {filepath}")

        try:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.warning(f"Download failed: {url} (HTTP {response.status})")
                    return False, f"HTTP {response.status}", None

                total_size = response.headers.get("Content-Length")
                downloaded = 0

                async with aiofiles.open(filepath, "wb") as f:
                    async for chunk in response.content.iter_chunked(self.chunk_size):
                        await f.write(chunk)
                        downloaded += len(chunk)

                logger.info(f"Download complete: {filename} ({downloaded} bytes)")
                return True, "Downloaded successfully", filename

        except asyncio.TimeoutError:
            logger.error(f"Download timeout: {url}")
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    logger.debug(f"Removed partial file: {filepath}")
                except Exception as e:
                    logger.warning(f"Failed to remove partial file {filepath}: {e}")
            return False, "Download timed out", None

        except aiohttp.ClientError as e:
            logger.error(f"Download connection error: {url} - {e}")
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            return False, f"Connection error: {str(e)[:50]}", None

        except Exception as e:
            logger.error(f"Download failed: {url} - {e}", exc_info=True)
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            return False, str(e)[:100], None

    async def download_batch(
        self,
        documents: List[DocumentInfo],
        download_path: str,
    ) -> AsyncGenerator[DownloadProgress, None]:
        """
        Download multiple files and yield progress updates.
        """
        logger.info(f"Starting batch download: {len(documents)} files to {download_path}")

        # Ensure directory exists
        success, result = ensure_directory_exists(download_path)
        if not success:
            logger.error(f"Cannot create download directory: {result}")
            yield DownloadProgress(
                status="error",
                message=f"Cannot create download directory: {result}",
            )
            return

        abs_path = result
        total_files = len(documents)
        files_completed = 0
        files_failed = 0

        connector = aiohttp.TCPConnector(limit=5)
        try:
            async with aiohttp.ClientSession(
                timeout=self.timeout,
                headers=self.headers,
                connector=connector,
            ) as session:
                for i, doc in enumerate(documents):
                    logger.debug(f"Processing file {i+1}/{total_files}: {doc.filename}")

                    yield DownloadProgress(
                        status="downloading",
                        current_file=doc.filename,
                        current_file_index=i + 1,
                        total_files=total_files,
                        files_completed=files_completed,
                        files_failed=files_failed,
                        message=f"Downloading {doc.filename}...",
                    )

                    success, message, saved_name = await self.download_file(
                        session, doc.url, abs_path, doc.filename
                    )

                    if success:
                        files_completed += 1
                    else:
                        files_failed += 1
                        logger.warning(f"Failed to download {doc.filename}: {message}")

                    await asyncio.sleep(0.1)

        except Exception as e:
            logger.error(f"Batch download error: {e}", exc_info=True)
            yield DownloadProgress(
                status="error",
                total_files=total_files,
                files_completed=files_completed,
                files_failed=files_failed,
                message=f"Download error: {str(e)}",
            )
            return

        logger.info(f"Batch download complete: {files_completed} succeeded, {files_failed} failed")

        yield DownloadProgress(
            status="complete",
            total_files=total_files,
            files_completed=files_completed,
            files_failed=files_failed,
            message=f"Downloaded {files_completed} files, {files_failed} failed",
        )


download_service = DownloadService()
