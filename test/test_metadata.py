from src.ingestion.metadata import extract_metadata


def test_extracts_longer_recommended_standard_prefix_first():
    metadata = extract_metadata(
        "添加剂标准.txt",
        "食品安全国家标准\nGB/T 1234-2025\n发布日期：2025年1月2日\n实施日期：2025年7月2日",
        "hash",
    )

    assert metadata.standard_code == "GB/T1234-2025"
    assert metadata.publish_date == "2025-01-02"
    assert metadata.effective_date == "2025-07-02"
    assert metadata.document_type == "国家标准"
