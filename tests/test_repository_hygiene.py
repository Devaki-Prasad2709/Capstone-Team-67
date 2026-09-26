from scripts.verify_repository import audit_layout, verify_tgnn_checkpoint


def test_repository_has_no_tracked_generated_or_raw_data() -> None:
    report = audit_layout()
    assert report["forbidden_tracked_files"] == 0


def test_tgnn_manifest_matches_checkpoint() -> None:
    report = verify_tgnn_checkpoint()
    assert len(report["sha256"]) == 64
    assert report["size_bytes"] > 0
