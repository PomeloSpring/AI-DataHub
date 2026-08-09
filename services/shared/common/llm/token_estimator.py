"""Token Estimator — improved token counting for mixed CN/EN content.

Uses different estimation strategies:
- Chinese characters: ~1.5 tokens per character
- English words: ~1.3 tokens per word
- Numbers/punctuation: ~0.5 tokens per character
- JSON: slightly more tokens due to structure
"""

import json
import re
from typing import Union


def estimate_tokens(text: str) -> int:
    """Estimate token count for mixed Chinese/English text.

    More accurate than simple len(text) // 3 for:
    - Chinese-heavy text (higher ratio)
    - JSON content (structure overhead)
    - Code/SQL (keyword density)
    """
    if not text:
        return 0

    # Count different character types
    chinese_chars = len(re.findall(r'[一-鿿]', text))
    english_words = len(re.findall(r'[a-zA-Z]+', text))
    numbers = len(re.findall(r'\d+', text))
    punctuation = len(re.findall(r'[^\w\s]', text))
    whitespace = len(re.findall(r'\s', text))

    # Estimate tokens per type
    # Chinese: ~1.5 tokens per character (BPE splits into sub-char units)
    # English: ~1.3 tokens per word (BPE splits common words)
    # Numbers: ~0.5 tokens per number group
    # Punctuation: ~0.5 tokens per punctuation
    # Whitespace: ~0.1 tokens per whitespace

    tokens = (
        chinese_chars * 1.5 +
        english_words * 1.3 +
        numbers * 0.5 +
        punctuation * 0.5 +
        whitespace * 0.1
    )

    return max(1, int(tokens))


def estimate_json_tokens(data: Union[dict, list]) -> int:
    """Estimate tokens for JSON data structures.

    Accounts for JSON structure overhead (brackets, keys, quotes).
    """
    if isinstance(data, str):
        return estimate_tokens(data)

    json_str = json.dumps(data, ensure_ascii=False, default=str)

    # JSON has overhead from structure
    base_tokens = estimate_tokens(json_str)

    # Add ~10% for JSON structure overhead
    return int(base_tokens * 1.1)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens in a message list.

    Handles different content types:
    - string content: direct estimation
    - list content (tool_use/tool_result): estimate each block
    - dict content: JSON serialization + estimation
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        role = msg.get("role", "user")

        # Role prefix overhead
        total += 2  # ~2 tokens for role marker

        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    # Tool use/result blocks
                    block_type = block.get("type", "")
                    if block_type == "tool_use":
                        total += 3  # type marker
                        total += estimate_tokens(block.get("name", ""))
                        total += estimate_json_tokens(block.get("input", {}))
                    elif block_type == "tool_result":
                        total += 3
                        content_val = block.get("content", "")
                        if isinstance(content_val, str):
                            total += estimate_tokens(content_val)
                        elif isinstance(content_val, list):
                            for item in content_val:
                                if isinstance(item, dict) and item.get("type") == "text":
                                    total += estimate_tokens(item.get("text", ""))
                    elif block_type == "text":
                        total += estimate_tokens(block.get("text", ""))
                    else:
                        total += estimate_json_tokens(block)
                elif hasattr(block, "text"):
                    total += estimate_tokens(block.text)
                elif hasattr(block, "input"):
                    total += estimate_json_tokens(block.input)
                else:
                    total += estimate_tokens(str(block))
        elif isinstance(content, dict):
            total += estimate_json_tokens(content)
        else:
            total += estimate_tokens(str(content))

    return total
