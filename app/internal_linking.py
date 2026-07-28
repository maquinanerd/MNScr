import logging
import re
from typing import Any, Dict, List, Set

from bs4 import BeautifulSoup

from app.config import PILAR_POSTS

logger = logging.getLogger(__name__)

EXCLUDED_TAGS = ['a', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote', 'code', 'pre', 'figure', 'figcaption']


def _post_title(post_data: Dict[str, Any]) -> str:
    title = (
        post_data.get("title")
        or post_data.get("titulo")
        or post_data.get("name")
        or ""
    )
    if title:
        return str(title).strip()
    for keyword in post_data.get("keywords") or []:
        keyword = str(keyword).strip()
        if keyword:
            return keyword
    return "Leia tambem"


def _append_fallback_link(soup: BeautifulSoup, link_option: Dict[str, Any], reason: str) -> bool:
    url = link_option.get("link") or link_option.get("url")
    if not url:
        return False

    anchor_text = _post_title(link_option)
    paragraph = soup.new_tag("p")
    paragraph.string = "Leia tambem: "
    anchor = soup.new_tag("a", href=url)
    anchor.string = anchor_text
    paragraph.append(anchor)

    paragraphs = soup.find_all("p")
    if len(paragraphs) >= 2:
        paragraphs[1].insert_after(paragraph)
    elif paragraphs:
        paragraphs[-1].insert_after(paragraph)
    elif soup.body:
        soup.body.append(paragraph)
    else:
        soup.append(paragraph)

    logger.info(
        "[INTERNAL_LINKING] fallback_inserted reason=%s anchor=%r url=%s",
        reason,
        anchor_text,
        url,
    )
    return True

def add_internal_links(
    html_content: str,
    link_map_data: Dict[str, List[Dict[str, Any]]],
    current_post_categories: List[int] = None,
    max_links: int = 6
) -> str:
    """
    Analyzes HTML and inserts internal links based on a prioritized strategy,
    using a list of keywords (title + tags) for each link.
    """
    if not html_content or not link_map_data or not link_map_data.get('posts'):
        return html_content

    soup = BeautifulSoup(html_content, 'html.parser')
    links_inserted = 0
    used_urls: Set[str] = set()

    all_link_options = link_map_data['posts']

    # --- Prioritization Logic ---
    pilar_options = []
    category_options = []
    other_options = []

    current_cat_set = set(current_post_categories or [])

    for post_data in all_link_options:
        # Skip if the post has no keywords to match
        if not post_data.get('keywords'):
            continue

        is_pilar = post_data['link'] in PILAR_POSTS
        shares_category = current_cat_set and not current_cat_set.isdisjoint(post_data.get('categories', []))

        if is_pilar:
            pilar_options.append(post_data)
        elif shares_category:
            category_options.append(post_data)
        else:
            other_options.append(post_data)

    # Within each priority group, sort keywords by length, descending.
    # This ensures we try to match "Real Madrid Club de Fútbol" before "Real Madrid".
    for group in [pilar_options, category_options, other_options]:
        for post_data in group:
            post_data['keywords'].sort(key=len, reverse=True)

    prioritized_link_options = pilar_options + category_options + other_options

    text_nodes = soup.find_all(string=True)

    for node in text_nodes:
        if links_inserted >= max_links:
            break

        if any(node.find_parent(tag) for tag in EXCLUDED_TAGS):
            continue

        original_text = str(node)
        modified_in_node = False

        for link_option in prioritized_link_options:
            if modified_in_node or links_inserted >= max_links:
                break

            url = link_option['link']
            if url in used_urls:
                continue

            # Iterate through all keywords for this link option (title, tags)
            for keyword in link_option['keywords']:
                pattern = re.compile(r'\b(' + re.escape(keyword) + r')\b', re.IGNORECASE)

                if pattern.search(original_text):
                    link_tag_str = f'<a href="{url}">{keyword}</a>'
                    new_html = pattern.sub(link_tag_str, original_text, count=1)

                    node.replace_with(BeautifulSoup(new_html, 'html.parser'))

                    links_inserted += 1
                    used_urls.add(url)
                    modified_in_node = True # Mark that we modified this node

                    priority = "PILAR" if link_option in pilar_options else "CATEGORY" if link_option in category_options else "OTHER"
                    logger.info(f"Inserted link for keyword: '{keyword}' (Priority: {priority})")
                    break # Stop searching keywords for this link_option

    if links_inserted == 0:
        fallback_option = None
        fallback_reason = ""
        if category_options:
            fallback_option = category_options[0]
            fallback_reason = "same_category_no_entity_match"
        elif pilar_options:
            fallback_option = pilar_options[0]
            fallback_reason = "pillar_no_entity_match"
        elif other_options:
            fallback_option = other_options[0]
            fallback_reason = "generic_no_entity_match"

        if fallback_option and _append_fallback_link(soup, fallback_option, fallback_reason):
            links_inserted = 1
        else:
            logger.info(
                "[INTERNAL_LINKING] candidates=0 reason=no_keyword_match_no_fallback current_categories=%s link_map_posts=%s",
                sorted(current_cat_set),
                len(all_link_options),
            )

    return str(soup)
