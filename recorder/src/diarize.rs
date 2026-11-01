//! Who spoke when in meeting audio, on this computer, after the recording stops. The microphone
//! is always "Me"; the other side of the call is one mixed channel, so it is diarized with
//! anarlog's pyannote-local (a Rust port of the pyannote 3.1 pipeline, vendored in
//! `vendor/anarlog`) and each word gets the voice that overlaps it most. When the transcription
//! service also labelled speakers inside a chunk, all words of one label take the voice most of
//! them got, then turns are joined across chunks by voiceprint (`link_turns`). See
//! `bench/README.md` for what each step measured.
use crate::{
    audio::Resampler,
    store::{Store, Word},
};
use anyhow::{Context, Result, ensure};
use pyannote_local::{
    AudioSource, DiarizationConfig, DiarizeRequest, Diarizer, SAMPLE_RATE, SpeakerBounds,
    SpeakerSegment,
};
use std::{collections::BTreeMap, path::PathBuf};

/// Meeting audio as one timeline, read chunk file by chunk file so long meetings stay on disk.
struct Timeline {
    pieces: Vec<(usize, usize, PathBuf)>,
    total: usize,
    loaded: Option<(usize, Vec<f32>)>,
}

fn per_ms() -> usize {
    SAMPLE_RATE as usize / 1000
}

fn decode(path: &PathBuf) -> Result<Vec<f32>> {
    let mut reader = hound::WavReader::open(path).context("Invalid WAV audio")?;
    let spec = reader.spec();
    let channels = usize::from(spec.channels.max(1));
    let samples: Vec<f32> = reader
        .samples::<i16>()
        .map(|value| value.map(|value| value as f32 / 32768.))
        .collect::<std::result::Result<_, _>>()?;
    let mono: Vec<f32> = samples
        .chunks(channels)
        .map(|frame| frame.iter().sum::<f32>() / frame.len() as f32)
        .collect();
    if spec.sample_rate == SAMPLE_RATE {
        return Ok(mono);
    }
    let mut output = vec![];
    Resampler::new(spec.sample_rate, SAMPLE_RATE)?.push(&mono, &mut output);
    Ok(output)
}

impl AudioSource for Timeline {
    fn sample_count(&mut self) -> std::result::Result<usize, pyannote_local::Error> {
        Ok(self.total)
    }

    fn read(
        &mut self,
        start: usize,
        out: &mut [f32],
    ) -> std::result::Result<(), pyannote_local::Error> {
        out.fill(0.);
        let end = start + out.len();
        for index in 0..self.pieces.len() {
            let (first, last, _) = &self.pieces[index];
            if *last <= start || *first >= end {
                continue;
            }
            if self
                .loaded
                .as_ref()
                .is_none_or(|(loaded, _)| *loaded != index)
            {
                let samples = decode(&self.pieces[index].2)
                    .map_err(|error| pyannote_local::Error::AudioRead(error.to_string()))?;
                self.loaded = Some((index, samples));
            }
            let (first, samples) = (
                self.pieces[index].0,
                &self.loaded.as_ref().expect("loaded").1,
            );
            for (offset, slot) in out.iter_mut().enumerate() {
                if let Some(sample) = (start + offset)
                    .checked_sub(first)
                    .and_then(|at| samples.get(at))
                {
                    *slot = *sample;
                }
            }
        }
        Ok(())
    }
}

/// Who spoke when in one WAV file, as (start s, end s, voice). For tuning against references.
pub fn diarize_file(
    path: &std::path::Path,
    config: DiarizationConfig,
    bounds: SpeakerBounds,
) -> Result<Vec<(f64, f64, usize)>> {
    let mut audio = decode(&path.to_path_buf())?;
    let found = Diarizer::new(config)?.diarize(
        &mut audio,
        &DiarizeRequest {
            bounds,
            ..Default::default()
        },
    )?;
    Ok(found
        .segments
        .iter()
        .map(|segment| (segment.start, segment.end, segment.speaker))
        .collect())
}

/// Labels meeting-audio words with "Speaker 1", "Speaker 2", … in order of first speech, or
/// clears the labels when only one voice is heard. Returns the number of voices.
pub fn label(store: &Store, id: &str) -> Result<usize> {
    // A looser threshold than pyannote's 0.7045 merges the stray extra voices the benchmark
    // found (`bench/README.md`) without merging real people.
    label_with(
        store,
        id,
        DiarizationConfig {
            clustering_threshold: 0.8,
            ..Default::default()
        },
        SpeakerBounds::default(),
        Some(LINK_DISTANCE),
    )
}

/// Cosine distance under which two transcription-service turns are the same person.
/// Chosen on the four tuning meetings alone, the middle of the best range 0.85–1.0 (`bench/README.md`).
pub const LINK_DISTANCE: f32 = 0.9;

/// `link`: join service turns by voiceprint under this distance (None: by local voices only).
pub fn label_with(
    store: &Store,
    id: &str,
    config: DiarizationConfig,
    bounds: SpeakerBounds,
    link: Option<f32>,
) -> Result<usize> {
    label_from(store, id, Some((config, bounds)), vec![], link)
}

/// Labels words from speaker segments found elsewhere (another diarizer, for comparison).
pub fn label_segments(
    store: &Store,
    id: &str,
    segments: Vec<SpeakerSegment>,
    link: Option<f32>,
) -> Result<usize> {
    label_from(store, id, None, segments, link)
}

fn label_from(
    store: &Store,
    id: &str,
    config: Option<(DiarizationConfig, SpeakerBounds)>,
    mut segments: Vec<SpeakerSegment>,
    link: Option<f32>,
) -> Result<usize> {
    let record = store.get(id)?;
    let chunks: Vec<_> = record
        .chunks
        .iter()
        .filter(|chunk| {
            chunk.channel == "them" && ["done", "silence"].contains(&chunk.status.as_str())
        })
        .collect();
    ensure!(
        chunks.iter().any(|chunk| !chunk.words.is_empty()),
        "No meeting audio words to label"
    );
    let mut pieces = vec![];
    for chunk in &chunks {
        pieces.push((
            chunk.start_ms as usize * per_ms(),
            chunk.end_ms as usize * per_ms(),
            store.audio_path(chunk)?,
        ));
    }
    let total = pieces.iter().map(|piece| piece.1).max().unwrap_or(0);
    let mut timeline = Timeline {
        pieces,
        total,
        loaded: None,
    };
    if let Some((config, bounds)) = config {
        segments = Diarizer::new(config)?
            .diarize(
                &mut timeline,
                &DiarizeRequest {
                    bounds,
                    ..Default::default()
                },
            )?
            .segments;
    }
    let words: Vec<(f64, f64)> = chunks
        .iter()
        .flat_map(|chunk| chunk.words.iter())
        .map(|word| {
            (
                word.start_ms as f64 / 1000.,
                word.end_ms.max(word.start_ms + 1) as f64 / 1000.,
            )
        })
        .collect();
    let voices = pyannote_local::assign_words(&words, &segments);
    let turns: Vec<(usize, &str, f64)> = chunks
        .iter()
        .enumerate()
        .flat_map(|(index, chunk)| chunk.words.iter().map(move |word| (index, word)))
        .zip(&words)
        .map(|((index, word), (start, end))| (index, word.turn.as_str(), end - start))
        .collect();
    let mut voices = follow_turns(&turns, voices);
    if let Some(distance) = link {
        let mut audio: BTreeMap<usize, Vec<f32>> = BTreeMap::new();
        let mut groups: BTreeMap<(usize, &str), Vec<usize>> = BTreeMap::new();
        for (index, (chunk, turn, _)) in turns.iter().enumerate() {
            if !turn.is_empty() {
                groups.entry((*chunk, turn)).or_default().push(index);
            }
        }
        let mut speech = vec![];
        for ((chunk, _), members) in &groups {
            if !audio.contains_key(chunk) {
                audio.insert(*chunk, decode(&store.audio_path(chunks[*chunk])?)?);
            }
            let samples = &audio[chunk];
            let offset = chunks[*chunk].start_ms as f64 / 1000.;
            let mut clip = vec![];
            for member in members {
                let (start, end) = words[*member];
                let at = |time: f64| {
                    (((time - offset).max(0.) * SAMPLE_RATE as f64) as usize).min(samples.len())
                };
                clip.extend_from_slice(&samples[at(start - 0.05)..at(end + 0.05)]);
            }
            speech.push((*chunk, members.clone(), clip));
        }
        voices = link_turns(speech, voices, distance)?;
    }
    // Number voices by when they first speak, so the first other person is Speaker 1.
    let mut names: BTreeMap<usize, String> = BTreeMap::new();
    for voice in voices.iter().flatten() {
        let next = names.len() + 1;
        names
            .entry(*voice)
            .or_insert_with(|| format!("Speaker {next}"));
    }
    let single = names.len() <= 1;
    let mut voices = voices.into_iter();
    let labels: Vec<(String, Vec<Word>)> = chunks
        .iter()
        .filter(|chunk| !chunk.words.is_empty())
        .map(|chunk| {
            let words = chunk
                .words
                .iter()
                .map(|word| Word {
                    speaker: match voices.next().flatten() {
                        Some(voice) if !single => names[&voice].clone(),
                        _ => String::new(),
                    },
                    ..word.clone()
                })
                .collect();
            (chunk.id.clone(), words)
        })
        .collect();
    store.label_speakers(id, &labels)?;
    Ok(names.len())
}

/// Joins transcription-service turns (one label within one chunk) into people: each turn with at
/// least a second of speech gets a WeSpeaker voiceprint, and turns are merged by average cosine
/// distance under `distance`, never two turns of one chunk (the service already told them apart).
/// Shorter turns and unlabelled words take the person their local voice mostly maps to.
/// `speech` is (chunk, word indexes, samples) per turn.
fn link_turns(
    speech: Vec<(usize, Vec<usize>, Vec<f32>)>,
    voices: Vec<Option<usize>>,
    distance: f32,
) -> Result<Vec<Option<usize>>> {
    let mut extractor = pyannote_local::EmbeddingExtractor::new()?;
    let mut turns = vec![];
    for (chunk, members, clip) in speech {
        let print = if clip.len() >= SAMPLE_RATE as usize {
            extractor.compute_optional(&clip)?
        } else {
            None
        };
        if let Some(print) = print {
            turns.push((chunk, members, print));
        }
    }
    let people = join(
        &turns
            .iter()
            .map(|(chunk, _, print)| (*chunk, print.clone()))
            .collect::<Vec<_>>(),
        distance,
    );
    let count = people.iter().max().map_or(0, |most| most + 1);
    let mut out = vec![None; voices.len()];
    // Local voice -> person it mostly became, for words no voiceprint covers.
    let mut votes: BTreeMap<usize, BTreeMap<usize, usize>> = BTreeMap::new();
    for ((_, members, _), person) in turns.iter().zip(people) {
        for member in members {
            out[*member] = Some(person);
            if let Some(voice) = voices[*member] {
                *votes.entry(voice).or_default().entry(person).or_default() += 1;
            }
        }
    }
    let mapped: BTreeMap<usize, usize> = votes
        .into_iter()
        .filter_map(|(voice, tally)| Some((voice, *tally.iter().max_by_key(|x| *x.1)?.0)))
        .collect();
    for (index, voice) in voices.iter().enumerate() {
        if out[index].is_none() {
            out[index] = voice.map(|voice| mapped.get(&voice).copied().unwrap_or(count + voice));
        }
    }
    Ok(out)
}

/// Average-linkage clustering of (chunk, voiceprint) by cosine distance under `distance`; items
/// of one chunk never share a cluster. Returns each item's cluster.
fn join(items: &[(usize, Vec<f32>)], distance: f32) -> Vec<usize> {
    use pyannote_local::clustering::{cosine_distance, normalize};
    let mut clusters: Vec<(Vec<usize>, Vec<usize>)> = (0..items.len())
        .map(|index| (vec![items[index].0], vec![index]))
        .collect();
    let prints: Vec<Vec<f32>> = items.iter().map(|(_, print)| normalize(print)).collect();
    let average = |a: &[usize], b: &[usize]| {
        let total: f32 = a
            .iter()
            .flat_map(|x| b.iter().map(|y| cosine_distance(&prints[*x], &prints[*y])))
            .sum();
        total / (a.len() * b.len()) as f32
    };
    loop {
        let mut best: Option<(f32, usize, usize)> = None;
        for i in 0..clusters.len() {
            for j in i + 1..clusters.len() {
                if clusters[i]
                    .0
                    .iter()
                    .any(|chunk| clusters[j].0.contains(chunk))
                {
                    continue;
                }
                let gap = average(&clusters[i].1, &clusters[j].1);
                if gap < distance && best.is_none_or(|(least, ..)| gap < least) {
                    best = Some((gap, i, j));
                }
            }
        }
        let Some((_, i, j)) = best else { break };
        let (chunks, members) = clusters.remove(j);
        clusters[i].0.extend(chunks);
        clusters[i].1.extend(members);
    }
    let mut out = vec![0; items.len()];
    for (cluster, (_, members)) in clusters.iter().enumerate() {
        for member in members {
            out[*member] = cluster;
        }
    }
    out
}

/// Gives every word of one (chunk, service label) the voice that covers most of its speech.
/// `turns` is (chunk, label, seconds) per word; words without a label keep their own voice.
fn follow_turns(turns: &[(usize, &str, f64)], voices: Vec<Option<usize>>) -> Vec<Option<usize>> {
    let mut votes: BTreeMap<(usize, &str), BTreeMap<usize, f64>> = BTreeMap::new();
    for ((chunk, turn, seconds), voice) in turns.iter().zip(&voices) {
        if let (false, Some(voice)) = (turn.is_empty(), voice) {
            *votes
                .entry((*chunk, turn))
                .or_default()
                .entry(*voice)
                .or_default() += seconds + 0.05;
        }
    }
    turns
        .iter()
        .zip(voices)
        .map(|((chunk, turn, _), voice)| {
            votes
                .get(&(*chunk, turn))
                .and_then(|tally| {
                    tally
                        .iter()
                        .max_by(|a, b| a.1.total_cmp(b.1))
                        .map(|(voice, _)| *voice)
                })
                .or(voice)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn service_labels_carry_the_majority_voice_within_a_chunk() {
        let turns = [
            (0, "0", 1.0),
            (0, "0", 1.0),
            (0, "0", 0.2),
            (0, "1", 0.5),
            (1, "0", 0.5),
            (1, "", 0.5),
        ];
        let voices = vec![Some(3), Some(3), Some(5), None, Some(5), Some(4)];
        assert_eq!(
            follow_turns(&turns, voices),
            [Some(3), Some(3), Some(3), None, Some(5), Some(4)]
        );
    }

    #[test]
    fn turns_join_by_voiceprint_but_never_within_one_chunk() {
        let items = [
            (0, vec![1., 0.]),
            (0, vec![0.9, 0.1]),
            (1, vec![1., 0.05]),
            (2, vec![0., 1.]),
        ];
        // The first two are alike but one chunk told them apart; the third joins the closer one.
        let got = join(&items, 0.5);
        assert_ne!(got[0], got[1]);
        assert_eq!(got[2], got[0]);
        assert_ne!(got[3], got[0]);
        assert_ne!(got[3], got[1]);
    }

    #[test]
    fn timeline_reads_across_chunk_files_and_fills_gaps_with_silence() {
        let folder = tempfile::tempdir().unwrap();
        let write = |name: &str, value: f32| {
            let path = folder.path().join(name);
            std::fs::write(
                &path,
                crate::audio::encode_wav(&[value; 16], SAMPLE_RATE).unwrap(),
            )
            .unwrap();
            path
        };
        let mut timeline = Timeline {
            pieces: vec![(0, 16, write("a.wav", 0.5)), (32, 48, write("b.wav", -0.5))],
            total: 48,
            loaded: None,
        };
        let mut out = [1.0; 24];
        timeline.read(12, &mut out).unwrap();
        assert!(out[..4].iter().all(|x| (x - 0.5).abs() < 0.01));
        assert!(out[4..20].iter().all(|x| *x == 0.));
        assert!(out[20..].iter().all(|x| (x + 0.5).abs() < 0.01));
    }
}
