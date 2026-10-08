"""Unit tests for track-level fusion."""

from ocr_engine.fusion import FrameRead, fuse_track_reads


def test_fusion_single_read():
    reads = [FrameRead(text="MH12AB1234", char_probs=[0.9] * 10, quality=0.8)]
    result = fuse_track_reads(reads)
    assert result.text == "MH12AB1234"
    assert len(result.char_conf) == 10
    assert result.confidence > 0


def test_fusion_majority_vote():
    reads = [
        FrameRead(text="MH12AB1234", char_probs=[0.95] * 10, quality=0.9),
        FrameRead(text="MH12AB1235", char_probs=[0.6] * 10, quality=0.5),
        FrameRead(text="MH12AB1234", char_probs=[0.92] * 10, quality=0.85),
    ]
    result = fuse_track_reads(reads)
    assert result.text == "MH12AB1234"
    assert result.confidence > 0.5


def test_fusion_empty():
    result = fuse_track_reads([])
    assert result.text == ""
    assert result.confidence == 0.0


def test_fusion_alternates():
    reads = [
        FrameRead(text="DL1CA1234", char_probs=[0.9] * 9, quality=0.8),
        FrameRead(text="DL1CB1234", char_probs=[0.7] * 9, quality=0.7),
    ]
    result = fuse_track_reads(reads, top_k=2)
    assert result.text
    assert isinstance(result.alternates, list)


def test_fusion_extra_char_read_does_not_shift_later_votes():
    truth = "MH12AB1234"
    reads = [FrameRead(text=truth, char_probs=[0.95] * 10, quality=0.9)] * 3
    reads.append(FrameRead(text="MH12AAB1234", char_probs=[0.95] * 11, quality=0.8))
    result = fuse_track_reads(reads)
    assert result.text == truth
    # Positions after the inserted char agree across all reads, so confidence stays high.
    assert min(result.char_conf[6:]) > 0.99


def test_fusion_prefers_agreed_text_over_longer_read():
    truth = "MH12AB1234"
    reads = [FrameRead(text=truth, char_probs=[0.95] * 10, quality=0.95)] * 3
    reads += [FrameRead(text="MH12AAB1234", char_probs=[0.95] * 11, quality=0.95)] * 2
    assert fuse_track_reads(reads).text == truth


def test_fusion_three_vs_four_digit_serial():
    # India decoder can emit both a 3- and 4-digit serial for the same plate.
    reads = [FrameRead(text="KL34A465", char_probs=[0.95] * 8, quality=0.9)] * 3
    reads += [FrameRead(text="KL34A4655", char_probs=[0.9] * 9, quality=0.9)]
    assert fuse_track_reads(reads).text == "KL34A465"
