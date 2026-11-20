"""Tests for sentrysearch.local_embedder (mocked — no torch required)."""

from unittest.mock import MagicMock, patch

import pytest

from sentrysearch.local_embedder import (
    LocalEmbedder, LocalModelError, MODEL_ALIASES,
    normalize_model_key, detect_default_model, _cpu_fallback_warning,
)


class TestModelAliases:
    def test_qwen8b_alias_resolves(self):
        embedder = LocalEmbedder(model_name="qwen8b")
        assert embedder._model_name == "Qwen/Qwen3-VL-Embedding-8B"

    def test_qwen2b_alias_resolves(self):
        embedder = LocalEmbedder(model_name="qwen2b")
        assert embedder._model_name == "Qwen/Qwen3-VL-Embedding-2B"

    def test_full_hf_id_passed_through(self):
        embedder = LocalEmbedder(model_name="Qwen/Qwen3-VL-Embedding-8B")
        assert embedder._model_name == "Qwen/Qwen3-VL-Embedding-8B"

    def test_custom_model_name_passed_through(self):
        embedder = LocalEmbedder(model_name="custom/my-model")
        assert embedder._model_name == "custom/my-model"

    def test_aliases_dict_has_expected_keys(self):
        assert "qwen8b" in MODEL_ALIASES
        assert "qwen2b" in MODEL_ALIASES


class TestLocalModelError:
    def test_is_runtime_error(self):
        assert issubclass(LocalModelError, RuntimeError)

    def test_message(self):
        err = LocalModelError("missing torch")
        assert "missing torch" in str(err)


class TestLocalEmbedderConstruction:
    def test_default_params(self):
        embedder = LocalEmbedder()
        assert embedder._model_name == "Qwen/Qwen3-VL-Embedding-8B"  # resolved from "qwen8b"
        assert embedder._dimensions == 768
        assert embedder._model is None

    def test_custom_params(self):
        embedder = LocalEmbedder(model_name="custom/model", dimensions=512)
        assert embedder._model_name == "custom/model"
        assert embedder._dimensions == 512

    def test_dimensions_method(self):
        embedder = LocalEmbedder(dimensions=1024)
        assert embedder.dimensions() == 1024

    def test_quantize_none_by_default(self):
        embedder = LocalEmbedder()
        assert embedder._quantize is None

    def test_quantize_true(self):
        embedder = LocalEmbedder(quantize=True)
        assert embedder._quantize is True

    def test_quantize_false(self):
        embedder = LocalEmbedder(quantize=False)
        assert embedder._quantize is False


def test_local_video_decodes_with_native_path(tmp_path, monkeypatch):
    """Real PyAV decoding catches malformed Windows file:// drive paths."""
    import subprocess
    from types import SimpleNamespace

    torch = pytest.importorskip("torch")
    pytest.importorskip("qwen_vl_utils")
    from qwen_vl_utils import vision_process
    from sentrysearch.chunker import _get_ffmpeg_executable

    path = tmp_path / "local video.mp4"
    subprocess.run([_get_ffmpeg_executable(), "-y", "-f", "lavfi", "-i",
                    "testsrc=size=64x64:rate=5:duration=2", "-pix_fmt", "yuv420p", str(path)],
                   capture_output=True, check=True)
    monkeypatch.setattr(vision_process, "FORCE_QWENVL_VIDEO_READER", "torchvision")
    vision_process.get_video_reader_backend.cache_clear()
    embedder = LocalEmbedder()
    monkeypatch.setattr(embedder, "_load_model", lambda: None)
    embedder._model = MagicMock()
    embedder._model.device = "cpu"
    embedder._model.return_value = SimpleNamespace(last_hidden_state=torch.ones((1, 1, 768)))
    embedder._processor = MagicMock()
    embedder._processor.return_value = {"attention_mask": torch.ones((1, 1), dtype=torch.long)}
    try:
        vector = embedder.embed_video_chunk(str(path))
        inputs = embedder._processor.call_args.kwargs
        assert inputs["videos"][0].shape[0] >= 2
        assert inputs["video_metadata"][0]["fps"] == 5
        assert len(vector) == 768
    finally:
        vision_process.get_video_reader_backend.cache_clear()


class TestLocalEmbedderLoadModel:
    def test_missing_torch_raises_local_model_error(self):
        embedder = LocalEmbedder()
        with patch.dict("sys.modules", {"torch": None}):
            with pytest.raises(LocalModelError, match="Missing dependencies"):
                embedder._load_model()

    def test_load_model_called_once(self):
        embedder = LocalEmbedder()
        embedder._model = MagicMock()  # pretend already loaded
        # Should return immediately without reloading
        embedder._load_model()


class TestCpuFallbackWarning:
    def test_intel_mac_points_to_gemini_backend(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr("platform.machine", lambda: "x86_64")

        warning = _cpu_fallback_warning()

        assert "Intel Mac" in warning
        assert "--backend gemini" in warning

    def test_linux_cpu_only_uses_general_warning(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setattr("platform.machine", lambda: "x86_64")

        warning = _cpu_fallback_warning()

        assert warning == "Warning: No GPU detected, local inference will be very slow."
        assert "--backend gemini" not in warning

    def test_apple_silicon_cpu_only_uses_general_warning(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setattr("platform.machine", lambda: "arm64")

        warning = _cpu_fallback_warning()

        assert warning == "Warning: No GPU detected, local inference will be very slow."
        assert "--backend gemini" not in warning


class TestNormalizeModelKey:
    def test_alias_returned_as_is(self):
        assert normalize_model_key("qwen8b") == "qwen8b"
        assert normalize_model_key("qwen2b") == "qwen2b"

    def test_full_hf_id_reversed_to_alias(self):
        assert normalize_model_key("Qwen/Qwen3-VL-Embedding-8B") == "qwen8b"
        assert normalize_model_key("Qwen/Qwen3-VL-Embedding-2B") == "qwen2b"

    def test_custom_model_sanitized(self):
        assert normalize_model_key("org/my-custom-model") == "org_my_custom_model"


class TestDetectDefaultModel:
    def test_no_torch_returns_qwen8b(self):
        with patch.dict("sys.modules", {"torch": None}):
            # ImportError path
            result = detect_default_model()
            assert result == "qwen8b"

    def test_cuda_returns_qwen8b(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = detect_default_model()
            assert result == "qwen8b"

    def test_cpu_only_returns_qwen2b(self):
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.backends.mps.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = detect_default_model()
            assert result == "qwen2b"


class TestLocalEmbedderMethods:
    def test_embed_query_calls_load_model(self):
        embedder = LocalEmbedder()
        embedder._load_model = MagicMock(
            side_effect=LocalModelError("no torch in CI")
        )
        with pytest.raises(LocalModelError):
            embedder.embed_query("test query")
        embedder._load_model.assert_called_once()

    def test_embed_video_chunk_calls_load_model(self):
        embedder = LocalEmbedder()
        embedder._load_model = MagicMock(
            side_effect=LocalModelError("no torch in CI")
        )
        with pytest.raises(LocalModelError):
            embedder.embed_video_chunk("/fake/path.mp4")
        embedder._load_model.assert_called_once()
