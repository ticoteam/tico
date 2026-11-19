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
    pub notes_stamp: String,
    pub scope: &'a str,
    pub resumed: bool,
    pub replacing: bool,
}

pub(super) enum End {
    Closed,
    Restart,
}

struct Turn {
    id: Option<String>,
    question: Option<String>,
    spoken: String,
    answer: String,
    next_message: bool,
}

impl Turn {
    fn new(question: Option<String>) -> Self {
        Self {
            id: None,
            question,
            spoken: String::new(),
            answer: String::new(),
            next_message: false,
        }
    }
}

async fn in_flight<F: Future>(future: &mut Option<Pin<Box<F>>>) -> F::Output {
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
            Some(Turn::new(None))
        };
        let mut poll = time::interval(Duration::from_millis(400));
        poll.set_missed_tick_behavior(time::MissedTickBehavior::Skip);
        let mut last = String::new();
        let mut interrupt_at = None;
        let mut closing = false;
        let mut warmed = start.resumed;
        let mut input_poll = None;
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
                            self.voice_event(d, "message", json!({"voice_turn": question, "text": reply, "final": true})).await?;
                        } else if ev["status"] == "completed" {
                            // A thread/start reply has no persisted rollout yet, and a half-finished primer
                            // cannot stand for a warm thread after a crash.
                            keep_session(&self.state, d.bot, start.scope, d.runtime, d.thread).await?;
                            warmed = true;
                        } else {
                            return Err(Fail::runtime("Codex did not complete the voice primer"));
                        }
                    } else if let Some(question) = turn.question.as_ref().filter(|_| interrupt_at.is_none()) {
                        if kind == "delta" {
                            if let Some(text) = ev["text"].as_str().filter(|text| !text.is_empty()) {
                                let piece = crate::hosts::add_piece(&mut turn.spoken, std::mem::take(&mut turn.next_message), text);
                                self.voice_event(d, "delta", json!({"voice_turn": question, "text": piece})).await?;
                            }
                        } else if kind == "message" {
                            if !turn.next_message && needs_sentence_boundary(&turn.spoken) {
                                self.voice_event(d, "delta", json!({"voice_turn": question, "text": " "})).await?;
                            }
                            turn.next_message = true;
                            turn.answer = text_of(&ev["text"]);
                        }
                    }
                }
                _ = async {
                    match interrupt_at {
                        Some(at) => time::sleep_until(at).await,
                        None => std::future::pending().await,
                    }
                } => return Ok(if closing { End::Closed } else { End::Restart }),
                _ = poll.tick(), if input_poll.is_none() && interrupt_at.is_none() => {
                    if d.flags.lost.load(Ordering::SeqCst) {
                        return Err(Fail::runtime("Voice session lease ended"));
                    }
                    let phase = if active.is_some() || starting.is_some() { "busy" } else { "ready" };
                    input_poll = Some(Box::pin(self.voice_inputs(d.aid, phase)));
                }
                input = in_flight(&mut input_poll) => {
                    input_poll = None;
                    let input = input?;
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
                            active = Some(Turn::new(Some(question)));
                        }
                    }
                }
            }
        }
    }
}
