# Vendored from anarlog

Speaker diarization crates copied from [fastrepl/anarlog](https://github.com/fastrepl/anarlog)
(MIT, see `LICENSE`) at commit `98d6219da3ad1f06c8175fb0f5e4e1867a6d6286`:

| Here | Upstream |
|---|---|
| `onnx` | `crates/onnx` |
| `embedding` | `crates/embedding` |
| `voiceprint` | `crates/voiceprint` |
| `pyannote-local` | `crates/pyannote-local` (eval binary and datasets left out) |

Changes: onnxruntime sessions use 4 intra-op threads instead of 1 (about 2x faster batch
diarization on the CPU path), concrete dependency versions instead of workspace ones, `anlg-*` package names, `pyannote-local`
re-exporting `EmbeddingExtractor`, and
`models.rs`, a build script that downloads the two ONNX models (pyannote segmentation 3.0, MIT;
WeSpeaker ResNet34 trained on VoxCeleb, CC BY 4.0) from that commit and checks their SHA-256.
The models are not committed. Upstream tests need anarlog's test data and are not run here.

To update, copy the crates from a newer commit, keep these Cargo.toml files, and update the
commit and checksums in `models.rs`.
