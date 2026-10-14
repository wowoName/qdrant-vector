"""Split original text, never average vectors or summarize away the tail."""
from dataclasses import replace


def split_chunks_by_tokens(chunks, tokenizer, max_tokens: int, overlap: int = 32):
    if max_tokens < 4:
        raise ValueError("Embedding token budget is too small")

    def fits(text):
        return len(tokenizer.encode(text, add_special_tokens=True, truncation=False)) <= max_tokens

    result = []
    for chunk in chunks:
        if fits(chunk.text):
            result.append(chunk)
            continue
        text = chunk.text
        start = 0
        pieces = []
        while start < len(text):
            lower, upper = 1, len(text) - start
            best = 0
            while lower <= upper:
                middle = (lower + upper) // 2
                if fits(text[start:start + middle]):
                    best = middle
                    lower = middle + 1
                else:
                    upper = middle - 1
            if not best:
                raise ValueError("One source character exceeds the embedding token budget")
            end = start + best
            if end < len(text):
                boundary = max(text.rfind(sep, start + best // 2, end) for sep in ("\n", "。", "；", ";", ". "))
                if boundary >= 0:
                    end = boundary + 1
            body = text[start:end]
            # Tokenization is not strictly monotonic; validate the final slice.
            while not fits(body) and end > start + 1:
                end -= 1
                body = text[start:end]
            if not fits(body):
                raise ValueError("Unable to fit source text within embedding token budget")
            metadata = dict(chunk.metadata)
            metadata.update({"source_chunk_index": chunk.index, "embedding_char_start": start, "embedding_char_end": end})
            exact_offsets = chunk.char_end - chunk.char_start == len(text)
            pieces.append(replace(
                chunk, text=body, metadata=metadata,
                char_start=chunk.char_start + start if exact_offsets else chunk.char_start,
                char_end=chunk.char_start + end if exact_offsets else chunk.char_end,
            ))
            if end == len(text):
                break
            start = max(start + 1, end - min(overlap, (end - start) // 4))
        for index, piece in enumerate(pieces):
            piece.metadata.update({"token_split_part": index, "token_split_parts": len(pieces)})
        result.extend(pieces)
    return result
