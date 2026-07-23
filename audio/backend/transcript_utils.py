import re


def normalize_transcript_for_compare(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", "", text).strip().lower()
    return re.sub(r"[，。,.!?！？:：;；\-_'\"“”‘’()（）]", "", text)


def collapse_repetitive_transcript(text: str) -> str:
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""

    # hello hello hello -> hello
    tokens = [tok for tok in re.split(r"\s+", text) if tok]
    if len(tokens) >= 2:
        normalized_tokens = [normalize_transcript_for_compare(tok) for tok in tokens]
        if normalized_tokens and all(tok and tok == normalized_tokens[0] for tok in normalized_tokens):
            return tokens[0]

    # 哦哦哦哦哦 / 哈哈哈哈 / aaaaa -> 压成较短版本
    normalized = normalize_transcript_for_compare(text)
    if normalized:
        unit = normalized[0]
        if len(set(normalized)) == 1 and len(normalized) >= 3:
            return unit

        match = re.fullmatch(r"(.{1,8}?)\1{1,}", normalized)
        if match:
            return match.group(1)

    return text
