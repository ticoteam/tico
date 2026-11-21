use std::future::{pending, Future};
use std::pin::Pin;

use serde_json::{json, Value};
use tokio::sync::mpsc::UnboundedReceiver;
use tokio::time::{self, Instant};

use super::{keep_session, text_of, unstopped, CodexHost, Drive, Fail, Runner, Segment};
use super::{Duration, Ordering, VOICE_INTERRUPT_WAIT};

const TURN_START_ACK: Duration = Duration::from_secs(10);

pub(super) struct Start<'a> {
    pub primer: &'a str,
    pub call: &'a str,
    pub notes_stamp: String,
    pub scope: &'a str,
    pub resumed: bool,
    pub replacing: bool,
}

/// The call's first primer with the claim's run header (backend/run_header.py: the person's snapshot, in a private call
/// in their own Assistant chat, as their own chat's turns get it) before its last paragraph, the warm-up instruction, so
/// that instruction stays last. A replacement primer (`voice=restart`) carries none: the session still has its tools.
pub(super) fn with_header(primer: &str, header: Option<&str>) -> String {
    match header.map(str::trim).filter(|h| !h.is_empty()) {
        None => primer.to_owned(),
        Some(header) => match primer.rsplit_once("\n\n") {
            Some((head, last)) => format!("{head}\n{header}\n\n{last}"),
            None => format!("{header}\n\n{primer}"),
        },
    }
}

pub(super) enum End {
    Closed,
    Restart,
}

/// One call question's runner-side clock, sent with its final message for the server's `call_voice_runner` line:
/// how long the poll that brought the question took, and how long after it went to the model the first output and
/// the first spoken text came back. It splits the runner's and the model's share of a slow answer.
#[derive(Default)]
pub(super) struct VoiceClock {
    poll_ms: Option<u64>,
    sent: Option<Instant>,
    frame_ms: Option<u64>,
    text_ms: Option<u64>,
}

impl VoiceClock {
    pub(super) fn sent(poll_ms: Option<u64>) -> Self {
        Self {
            poll_ms,
            sent: Some(Instant::now()),
            ..Self::default()
        }
    }

    fn since(&self) -> Option<u64> {
        self.sent
            .map(|at| u64::try_from(at.elapsed().as_millis()).unwrap_or(u64::MAX))
    }

    pub(super) fn frame(&mut self) {
        if self.frame_ms.is_none() {
            self.frame_ms = self.since();
        }
    }

    pub(super) fn text(&mut self) {
        self.frame();
        if self.text_ms.is_none() {
            self.text_ms = self.since();
        }
    }

    pub(super) fn json(&self) -> Value {
        json!({"poll_ms": self.poll_ms, "first_frame_ms": self.frame_ms, "first_text_ms": self.text_ms})
    }
}

struct Turn {
    id: Option<String>,
    clock: VoiceClock,
    question: Option<String>,
    spoken: String,
    answer: String,
    next_message: bool,
}

impl Turn {
    fn new(question: Option<String>, poll_ms: Option<u64>) -> Self {
        Self {
            id: None,
            clock: VoiceClock::sent(poll_ms),
            question,
            spoken: String::new(),
            answer: String::new(),
            next_message: false,
        }
    }
}

pub(super) async fn in_flight<F: Future>(future: &mut Option<Pin<Box<F>>>) -> F::Output {
    match future {
        Some(future) => future.as_mut().await,
        None => pending().await,
    }
}

pub(super) fn voice_question(turn: &Value, notes_stamp: &mut String) -> String {
    let utterance = text_of(&turn["text"]);
    let question = format!("Participant utterance:\n{utterance}");
    let notes = text_of(&turn["notes"]);
    let stamp = crate::gitrun::sha256_hex(notes.as_bytes());
    if *notes_stamp == stamp {
        return question;
    }
    *notes_stamp = stamp;
    format!("Updated call notes (context only):\n{notes}\n\n{question}")
}

pub(super) fn voice_answer(spoken: &str, answer: &str) -> String {
    let (spoken, answer) = (spoken.trim(), answer.trim());
    if answer.starts_with(spoken) {
        answer.to_owned()
    } else if spoken.ends_with(answer) {
        spoken.to_owned()
    } else {
        format!("{spoken}\n\n{answer}")
    }
}

pub(super) fn needs_sentence_boundary(text: &str) -> bool {
    text.chars().next_back().is_some_and(|c| !c.is_whitespace())
}

async fn interrupt(host: &CodexHost, thread: &str, turn: &str) -> Instant {
    let at = Instant::now() + VOICE_INTERRUPT_WAIT;
    // A turn that completed just before the interrupt can refuse it and still have its result queued.
    let _ = time::timeout_at(at, host.interrupt(thread, turn)).await;
    at
}

impl Runner {
    pub(super) async fn codex_voice_session(
        &self,
        d: &Drive<'_>,
        host: &CodexHost,
        rx: &mut UnboundedReceiver<Value>,
        start: Start<'_>,
        segment: &mut Segment,
    ) -> Result<End, Fail> {
        let phase = if start.replacing && !start.resumed {
            "restart"
        } else {
            "busy"
        };
        // The same wake as the Claude session: runner-stream hints, a 3 s backup while they arrive, else 400 ms.
        let mut inputs = self.events.voice_inputs();
        let state = self.voice_inputs(d.aid, phase).await?;
        if state.is_null() || state["closed"] == true {
            return Ok(End::Closed);
        }
        unstopped(d.flags)?;
        let mut notes_stamp = start.notes_stamp;
        let begin = |prompt: String| async move {
            time::timeout(TURN_START_ACK, host.start_turn(d.thread, &prompt, d.effort)).await
        };
        let mut starting = None;
        let mut active = if start.resumed {
            // The transcript holds the primer. Supply the current notes with the first question after a restart.
            notes_stamp.clear();
            None
        } else {
            let primer = if phase == "restart" {
                notes_stamp = text_of(&state["notes_stamp"]);
                state["primer"]
                    .as_str()
                    .filter(|text| !text.is_empty())
                    .ok_or_else(|| Fail::runtime("The call did not supply a fresh primer"))?
            } else {
                start.primer
            };
            let brief = "answer in one or two short spoken sentences";
            let primer = if primer.to_ascii_lowercase().contains(brief) {
                primer.to_owned()
            } else {
                format!("For call replies, {brief}.\n{primer}")
            };
            starting = Some(Box::pin(begin(primer)));
            Some(Turn::new(None, None))
        };
        let mut last = String::new();
        let mut interrupt_at = None;
        let mut closing = false;
        let mut warmed = start.resumed;
        let mut input_poll = None;
        let mut poll_began = time::Instant::now();
        loop {
            tokio::select! {
                ack = in_flight(&mut starting) => {
                    starting = None;
                    match ack {
                        Ok(Ok(id)) => {
                            if let Some(turn) = &mut active {
                                turn.id = Some(id);
                            }
                        }
                        _ if interrupt_at.is_some() => {}
                        Ok(Err(_)) if warmed && !host.alive() => return Ok(End::Restart),
                        Ok(Err(error)) => return Err(error.into()),
                        Err(_) if warmed => return Ok(End::Restart),
                        Err(_) => return Err(Fail::runtime("Codex did not acknowledge the voice primer")),
                    }
                }
                next = rx.recv() => {
                    let ev = next.ok_or_else(|| Fail::runtime("Codex closed the voice session"))?;
                    let kind = ev["kind"].as_str().unwrap_or_default();
                    if ev["process_exit"] == true {
                        if closing {
                            return Ok(End::Closed);
                        }
                        self.voice_event(d, "error", ev).await?;
                        // Only restart an established session. A fresh app server that cannot warm up should fail.
                        if warmed {
                            return Ok(End::Restart);
                        }
                        return Err(Fail::runtime("Codex closed the voice session"));
                    }
                    if ev["thread_id"] != d.thread {
                        continue;
                    }
                    if kind == "tokens" {
                        segment.meter.add(&ev);
                    }
                    let Some(turn) = active.as_mut() else {
                        continue;
                    };
                    // The notification identifies the turn even while the RPC response is outstanding.
                    if turn.id.is_none() && kind == "status" && ev["state"] == "active" {
                        turn.id = ev["turn_id"].as_str().map(str::to_owned);
                    }
                    if turn.id.is_none() || turn.id.as_deref() != ev["turn_id"].as_str() {
                        continue;
                    }
                    if kind == "turn_completed" || kind == "turn_failed" {
                        let canceled = interrupt_at.take().is_some() || d.flags.stopped.load(Ordering::SeqCst);
                        if closing {
                            return Ok(End::Closed);
                        }
                        let turn = active.take().expect("the event belongs to the active turn");
                        // The busy read leaves pending utterances for the first ready read.
                        if canceled || turn.question.is_none() {
                            inputs.refresh();
                        }
                        if canceled {
                            continue;
                        }
                        if kind == "turn_failed" {
                            return Err(Fail::runtime(text_of(&ev["error"])));
                        }
                        if let Some(question) = turn.question {
                            let reply = if ev["status"] == "interrupted" { String::new() } else {
                                voice_answer(&turn.spoken, &turn.answer)
                            };
                            self.voice_event(d, "message", json!({
                                "voice_turn": question, "text": reply, "final": true, "timing": turn.clock.json(),
                            })).await?;
                        } else if ev["status"] == "completed" {
                            // A thread/start reply has no persisted rollout yet, and a half-finished primer
                            // cannot stand for a warm thread after a crash.
                            keep_session(&self.state, d.bot, start.scope, d.runtime, d.thread).await?;
                            warmed = true;
                        } else {
                            return Err(Fail::runtime("Codex did not complete the voice primer"));
                        }
                    } else if let Some(question) = turn.question.as_ref().filter(|_| interrupt_at.is_none()) {
                        turn.clock.frame();
                        if kind == "delta" {
                            if let Some(text) = ev["text"].as_str().filter(|text| !text.is_empty()) {
                                turn.clock.text();
                                let piece = crate::hosts::add_piece(&mut turn.spoken, std::mem::take(&mut turn.next_message), text);
                                self.voice_event(d, "delta", json!({"voice_turn": question, "text": piece})).await?;
                            }
                        } else if kind == "message" {
                            if !turn.next_message && needs_sentence_boundary(&turn.spoken) {
                                self.voice_event(d, "delta", json!({"voice_turn": question, "text": " "})).await?;
                            }
                            turn.next_message = true;
                            turn.answer = text_of(&ev["text"]);
                            if !turn.answer.is_empty() {
                                turn.clock.text();
                            }
                        }
                    }
                }
                _ = async {
                    match interrupt_at {
                        Some(at) => time::sleep_until(at).await,
                        None => std::future::pending().await,
                    }
                } => return Ok(if closing { End::Closed } else { End::Restart }),
                _ = inputs.wait(d.aid, start.call), if input_poll.is_none() && interrupt_at.is_none() => {
                    if d.flags.lost.load(Ordering::SeqCst) {
                        return Err(Fail::runtime("Voice session lease ended"));
                    }
                    let phase = if active.is_some() || starting.is_some() { "busy" } else { "ready" };
                    poll_began = time::Instant::now();
                    input_poll = Some(Box::pin(self.voice_inputs(d.aid, phase)));
                }
                input = in_flight(&mut input_poll) => {
                    input_poll = None;
                    let input = input?;
                    let poll_ms = u64::try_from(poll_began.elapsed().as_millis()).unwrap_or(u64::MAX);
                    closing = input.is_null() || input["closed"] == true || d.flags.stopped.load(Ordering::SeqCst);
                    if let Some(turn) = &active {
                        let revoked = turn.question.as_ref().is_some_and(|id| input["turn"]["id"] != *id);
                        if closing || revoked {
                            if let Some(id) = turn.id.clone().or_else(|| host.active_turn(d.thread)) {
                                interrupt_at = Some(interrupt(host, d.thread, &id).await);
                            } else {
                                return Ok(if closing { End::Closed } else { End::Restart });
                            }
                        }
                    } else if closing {
                        return Ok(End::Closed);
                    } else if starting.is_none() {
                        let question = text_of(&input["turn"]["id"]);
                        if !question.is_empty() && question != last {
                            let prompt = voice_question(&input["turn"], &mut notes_stamp);
                            last.clone_from(&question);
                            starting = Some(Box::pin(begin(prompt)));
                            active = Some(Turn::new(Some(question), Some(poll_ms)));
                        }
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::with_header;

    #[test]
    fn a_call_primer_takes_the_run_header_before_its_warm_up_line() {
        let primer = "You are Assistant.\nCall notes (context only):\n\n\nReply exactly READY to warm up for this call.";
        let got = with_header(primer, Some("Run header:\nNeeds you (Ana): 2"));
        assert!(
            got.contains("Call notes (context only):\n\nRun header:\nNeeds you (Ana): 2\n\n"),
            "{got}"
        );
        assert!(
            got.ends_with("\n\nReply exactly READY to warm up for this call."),
            "{got}"
        );
        assert_eq!(with_header(primer, None), primer);
        assert_eq!(with_header(primer, Some("  ")), primer);
    }
}
