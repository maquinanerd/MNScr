"""
Media handling module for images and videos
"""

import logging
import os
import hashlib
from typing import Optional, Dict, Any
from urllib.parse import urlparse, urljoin

import requests
from PIL import Image
import io

from . import wordpress

logger = logging.getLogger(__name__)


class MediaHandler:
    """Handle media files (images/videos) according to pipeline configuration"""
    
    def __init__(self, pipeline_config: Dict[str, Any], wp_client: 'wordpress.WordPressClient'):
        self.config = pipeline_config
        self.wp_client = wp_client
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def _validate_image_url(self, url: str) -> bool:
        """Validate if URL points to a valid image"""
        if not url:
            return False
        
        # Check file extension
        parsed = urlparse(url)
        path = parsed.path.lower()
        valid_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
        
        if not any(path.endswith(ext) for ext in valid_extensions):
            return False
        
        # Skip very small images (likely icons/ads)
        if any(size in url.lower() for size in ['16x16', '32x32', '64x64', 'icon', 'favicon']):
            return False
        
        return True
    
    def _head_is_image(self, url: str, timeout: int = 8) -> bool:
        """
        Performs a HEAD request to check if the URL points to a real image
        and meets minimum size requirements.
        """
        if not url:
            return False
        try:
            # Use the class's session for consistent headers
            response = self.session.head(url, allow_redirects=True, timeout=timeout)
            response.raise_for_status()  # Will raise for 4xx/5xx responses
            
            content_type = response.headers.get('Content-Type', '')
            content_length = int(response.headers.get('Content-Length', '0') or 0)
            
            if content_type.startswith('image/') and content_length > 5 * 1024:  # 5KB
                logger.debug(f"HEAD check passed for {url} (Type: {content_type}, Size: {content_length})")
                return True
            else:
                logger.warning(f"HEAD check failed for {url}. Content-Type: '{content_type}', Size: {content_length} bytes.")
                return False
        except requests.exceptions.RequestException as e:
            logger.warning(f"HEAD request for image failed: {url} ({e})")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during image HEAD check for {url}: {e}", exc_info=True)
            return False

    def _download_image(self, url: str) -> Optional[bytes]:
        """Download image from URL"""
        try:
            logger.debug(f"Downloading image: {url}")
            
            response = self.session.get(url, timeout=15, stream=True)
            response.raise_for_status()
            
            # Check content type
            content_type = response.headers.get('content-type', '')
            if not content_type.startswith('image/'):
                logger.warning(f"URL does not return image content: {url}")
                return None
            
            # Download with size limit (10MB)
            max_size = 10 * 1024 * 1024
            content = b''
            
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    content += chunk
                    if len(content) > max_size:
                        logger.warning(f"Image too large, skipping: {url}")
                        return None
            
            return content
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error downloading image {url}: {str(e)}")
            return None
    
    def _validate_image_content(self, image_data: bytes) -> bool:
        """Validate downloaded image content"""
        try:
            with Image.open(io.BytesIO(image_data)) as img:
                # Check minimum dimensions
                width, height = img.size
                if width < 100 or height < 100:
                    logger.debug("Image too small, skipping")
                    return False
                
                # Check if it's a reasonable image
                if width > 5000 or height > 5000:
                    logger.debug("Image too large, skipping")
                    return False
                
                return True
                
        except Exception as e:
            logger.error(f"Error validating image: {str(e)}")
            return False
    
    def _upload_to_wordpress(self, image_data: bytes, filename: str) -> Optional[int]:
        """Upload image to WordPress media library"""
        try:
            # Generate filename if not provided
            if not filename:
                filename = f"image_{hashlib.md5(image_data).hexdigest()[:8]}.jpg"
            
            # Clean filename
            filename = os.path.basename(filename)
            if not filename.lower().endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                filename += '.jpg'
            
            media_id = self.wp_client.upload_media(image_data, filename)
            if media_id:
                logger.info(f"Successfully uploaded image to WordPress: {filename} (ID: {media_id})")
                return media_id
            
        except Exception as e:
            logger.error(f"Error uploading image to WordPress: {str(e)}")
        
        return None
    
    def handle_main_image(self, image_url: str) -> Optional[int]:
        """Handle main/featured image according to configuration"""
        if not image_url or not self._validate_image_url(image_url):
            return None
        
        images_mode = self.config.get('images_mode', 'hotlink')
        
        if images_mode == 'hotlink':
            logger.debug("Using hotlink mode for main image. No upload needed.")
            return None  # WordPress will use the URL directly
        
        elif images_mode == 'download_upload':
            # Perform HEAD check before attempting to download and upload
            if not self._head_is_image(image_url):
                logger.info(f"Featured image candidate failed HEAD check, skipping upload: {image_url}")
                return None

            logger.info(f"Downloading and uploading main image: {image_url}")
            
            # Download image
            image_data = self._download_image(image_url)
            if not image_data:
                return None
            
            # Validate content
            if not self._validate_image_content(image_data):
                return None
            
            # Generate filename from URL
            parsed_url = urlparse(image_url)
            filename = os.path.basename(parsed_url.path)
            
            # Upload to WordPress
            return self._upload_to_wordpress(image_data, filename)
        
        else:
            logger.warning(f"Unknown images_mode: {images_mode}")
            return None
    
    def process_content_images(self, content: str) -> str:
        """Process images in content according to configuration"""
        images_mode = self.config.get('images_mode', 'hotlink')
        
        if images_mode == 'hotlink':
            return content  # No processing needed
        
        elif images_mode == 'download_upload':
            # For now, keep original URLs in content
            # Advanced implementation would replace img src URLs with WordPress media URLs
            return content
        
        return content
    
    def get_attribution_text(self, source_url: str) -> str:
        """Generate attribution text for source"""
        attribution_policy = self.config.get('attribution_policy', 'Via {domain}')
        
        try:
            parsed = urlparse(source_url)
            domain = parsed.netloc
            
            # Clean domain (remove www.)
            if domain.startswith('www.'):
                domain = domain[4:]
            
            return attribution_policy.format(domain=domain)
            
        except Exception:
            return f"Via {source_url}"
    
    def add_attribution(self, content: str, source_url: str) -> str:
        """Add attribution to content"""
        attribution = self.get_attribution_text(source_url)
        
        # Add attribution at the end of content
        if content and attribution:
            content = f"{content}\n\n<p><em>{attribution}</em></p>"
        
        return content
