import asyncio
from types import SimpleNamespace

import pytest

from app.chunkers.base import ChunkItem
from app.chunkers.fixed_size_chunker import FixedSizeChunker
from app.chunkers.recursive_chunker import RecursiveChunker
from app.processors.pdf_processor import PDFProcessor
from app.utils.embedding_chunks import split_chunks_by_tokens
from app.utils.text_cleaner import clean_pdf_text


def assert_coverage(text, chunks):
    covered = set()
    for chunk in chunks:
        assert chunk.text == text[chunk.char_start:chunk.char_end]
        covered.update(range(chunk.char_start, chunk.char_end))
    assert all(i in covered for i, char in enumerate(text) if not char.isspace())


@pytest.mark.parametrize("chunker_type", [RecursiveChunker, FixedSizeChunker])
@pytest.mark.parametrize("text", ["甲" * 100 + "尾注7", "A\n\n" + "B" * 83 + "\n\nC", "数字\n2026\n剂量\n5\nmg/m²\n" * 12])
def test_chunk_bound_order_tail_and_coverage(chunker_type, text):
    chunker = chunker_type(chunk_size=40, chunk_overlap=5, min_size=20)
    chunks = chunker.chunk(text)
    assert chunks == list(chunker.chunk_stream(text))
    assert [chunk.char_start for chunk in chunks] == sorted(chunk.char_start for chunk in chunks)
    assert all(len(chunk.text) <= 40 for chunk in chunks)
    assert_coverage(text, chunks)


def test_zero_overlap_and_short_page_are_preserved():
    chunker = RecursiveChunker(chunk_size=40, chunk_overlap=0, min_size=30)
    assert chunker.chunk_overlap == 0
    chunks = chunker.chunk_with_pages(["诊断：肺炎", "Z" * 90 + "尾"])
    assert chunks[0].page_num == 0 and "肺炎" in chunks[0].text
    assert chunks[-1].page_num == 1 and chunks[-1].text.endswith("尾")


def test_pdf_cleaner_preserves_numbers_units_and_repetition():
    text = "检验结果\n2026\n5\nmg/m²\n检验结果\n病史\n检查\n"
    assert clean_pdf_text(text) == text.strip()


def test_per_type_params_reach_nested_parent_chunker():
    processor = PDFProcessor(chunk_strategy="parent_child")
    chunker = processor._get_chunker()
    assert chunker._child_chunker.chunk_size == processor._get_chunk_config()["size"]
    assert chunker._child_chunker.min_size == processor._get_chunk_config()["min_size"]


class CharacterTokenizer:
    def encode(self, text, **kwargs):
        return [0] + list(text) + [1]


def test_token_splitting_covers_tail_and_preserves_metadata():
    text = "甲" * 110 + "最后的诊断"
    original = ChunkItem(text, 2, 100, 100 + len(text), page_num=3, metadata={"sheet_name": "source"})
    pieces = split_chunks_by_tokens([original], CharacterTokenizer(), 42, overlap=5)
    assert len(pieces) > 1
    assert all(len(piece.text) + 2 <= 42 for piece in pieces)
    assert all(piece.page_num == 3 and piece.metadata["sheet_name"] == "source" for piece in pieces)
    assert pieces[-1].text.endswith("最后的诊断")
    assert original.metadata == {"sheet_name": "source"}
    covered = set()
    for piece in pieces:
        covered.update(range(piece.char_start - 100, piece.char_end - 100))
    assert covered == set(range(len(text)))


def test_stream_split_indexes_are_unique_across_batches():
    from app.services.document_tasks import _embedding_safe_stream

    class Provider:
        async def prepare_chunks(self, chunks):
            return split_chunks_by_tokens(chunks, CharacterTokenizer(), 32)

    async def run():
        source = [ChunkItem("X" * 60 + str(i), i) for i in range(70)]
        return [chunk async for chunk in _embedding_safe_stream(source, Provider())]

    chunks = asyncio.run(run())
    assert len(chunks) > 70
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


def test_embedding_rejects_oversize_without_encoding():
    from app.providers.embedding.huggingface_embedding import HuggingFaceEmbedding

    class Tokenizer(CharacterTokenizer):
        def __call__(self, texts, **kwargs):
            return {"input_ids": [self.encode(text) for text in texts]}

    provider = HuggingFaceEmbedding("test")
    provider._model = SimpleNamespace(tokenizer=Tokenizer(), max_seq_length=10)
    with pytest.raises(ValueError, match="禁止静默截断"):
        asyncio.run(provider.embed_texts(["X" * 11]))


def test_real_local_tokenizer_splits_chinese_pdf_without_truncation():
    from pathlib import Path
    from transformers import AutoTokenizer
    path = Path(__file__).resolve().parents[1] / "data/models/bge-small-zh-v1.5"
    if not path.exists():
        pytest.skip("Local tokenizer not installed")
    tokenizer = AutoTokenizer.from_pretrained(str(path), local_files_only=True)
    text = "患者出现发热咳嗽。" * 100 + "最终结论：需复查。"
    pieces = split_chunks_by_tokens([ChunkItem(text, 0, 0, len(text))], tokenizer, 512)
    assert len(pieces) > 1
    assert all(len(tokenizer.encode(piece.text, truncation=False)) <= 512 for piece in pieces)
    assert pieces[-1].text.endswith("最终结论：需复查。")
    assert_coverage(text, pieces)
