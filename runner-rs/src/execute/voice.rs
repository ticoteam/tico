use serde_json::{json, Value};
use tokio::sync::mpsc::UnboundedReceiver;
use tokio::time::{self, Instant};

use super::{keep_session, text_of, unstopped, CodexHost, Drive, Fail, Runner, Segment};
use super::{Duration, Ordering, VOICE_INTERRUPT_WAIT};

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
    id: String,
    question: Option<String>,
    spoken: String,
    answer: String,
    next_message: bool,
}

impl Turn {
    async fn start(
        host: &CodexHost,
        d: &Drive<'_>,
        text: &str,
        question: Option<String>,
    ) -> Result<Self, Fail> {
        Ok(Self {
            id: host.start_turn(d.thread, text, d.effort).await?,
            question,
            spoken: String::new(),
            answer: String::new(),
            next_message: false,
        })
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
            Some(Turn::start(host, d, &primer, None).await?)
        };
        let mut poll = time::interval(Duration::from_millis(400));
        poll.set_missed_tick_behavior(time::MissedTickBehavior::Skip);
        let mut last = String::new();
        let mut interrupt_at = None;
        let mut closing = false;
        let mut warmed_here = false;
        loop {
            tokio::select! {
                next = rx.recv() => {
                    let ev = next.ok_or_else(|| Fail::runtime("Codex closed the voice session"))?;
                    let kind = ev["kind"].as_str().unwrap_or_default();
                    if ev["process_exit"] == true {
                        if closing {
                            return Ok(End::Closed);
                        }
                        self.voice_event(d, "error", ev).await?;
                        // Only restart an established session. A fresh app server that cannot warm up should fail.
                        if warmed_here || !last.is_empty() {
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
                    let Some(turn) = active.as_mut().filter(|turn| ev["turn_id"] == turn.id) else {
                        continue;
                    };
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
                            warmed_here = true;
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
                _ = poll.tick(), if interrupt_at.is_none() => {
                    if d.flags.lost.load(Ordering::SeqCst) {
                        return Err(Fail::runtime("Voice session lease ended"));
                    }
                    let phase = if active.is_some() { "busy" } else { "ready" };
                    let input = self.voice_inputs(d.aid, phase).await?;
                    closing = input.is_null() || input["closed"] == true || d.flags.stopped.load(Ordering::SeqCst);
                    if let Some(turn) = &active {
                        let revoked = turn.question.as_ref().is_some_and(|id| input["turn"]["id"] != *id);
                        if closing || revoked {
                            interrupt_at = Some(interrupt(host, d.thread, &turn.id).await);
                        }
                    } else if closing {
                        return Ok(End::Closed);
                    } else {
                        let question = text_of(&input["turn"]["id"]);
                        if !question.is_empty() && question != last {
                            let prompt = voice_question(&input["turn"], &mut notes_stamp);
                            last.clone_from(&question);
                            match Turn::start(host, d, &prompt, Some(question)).await {
                                Ok(turn) => active = Some(turn),
                                Err(_) if !host.alive() => return Ok(End::Restart),
                                Err(error) => return Err(error),
                            }
                        }
                    }
                }
            }
        }
    }
}
