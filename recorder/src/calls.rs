//! Tico calls: a Google Meet started from a Tico thread opens the Recorder with
//! `tico-recorder://record?server=<org address>&call=<call id>`. One Recorder per call is the recording source
//! (`POST /api/v2/calls/{id}/recorder`, first claim wins); it records into the call's own live meeting, so no live
//! meeting is created here, and Tico posts the transcript in the thread when the call ends.
use crate::{
    network::{OLD_TICO, Tico},
    store::{Delivery, Store},
};
use anyhow::Result;
use serde_json::{Value, json};

/// What Tico answered a claim.
#[derive(Clone, Debug, PartialEq)]
pub enum Claim {
    /// This Recorder is the call's recording source: record into `meeting_id`, named `title`.
    Source {
        meeting_id: String,
        title: String,
    },
    /// Someone else's Recorder records the call; this one records nothing.
    NotSource,
    /// This Tico has no Meet calls (an older server answers 404): nothing new happens.
    NoCalls,
    Failed(String),
}

/// Reads the claim's answer (or its error, as the job reports it).
pub fn claim_outcome(result: Result<Value, String>) -> Claim {
    match result {
        Ok(reply) if reply["source"] == true => match reply["meeting_id"].as_str() {
            Some(meeting) if !meeting.trim().is_empty() => Claim::Source {
                meeting_id: meeting.to_owned(),
                title: reply["title"]
                    .as_str()
                    .unwrap_or_default()
                    .trim()
                    .to_owned(),
            },
            _ => Claim::Failed("Tico did not return the call's meeting".into()),
        },
        Ok(_) => Claim::NotSource,
        Err(error) if error.contains(OLD_TICO) || error.contains("HTTP 404 (") => Claim::NoCalls,
        Err(error) => Claim::Failed(error),
    }
}

/// Marks a call's recording as in Tico for `destination`, so it is listed as saved there and never imported as a
/// meeting of its own: Tico files the call's transcript itself. `meeting_id` is the meeting Tico filed, when known.
pub fn mark_sent(
    store: &Store,
    id: &str,
    destination: &str,
    call: &str,
    meeting_id: &str,
) -> Result<()> {
    let record = store.get(id)?;
    if let Some(entry) = record
        .deliveries
        .iter()
        .find(|entry| entry.destination_id == destination && entry.status == "sent")
    {
        if entry.meeting_id.is_empty() && !meeting_id.is_empty() {
            let mut entry = entry.clone();
            entry.meeting_id = meeting_id.into();
            store.delivery(id, &entry)?;
        }
        return Ok(());
    }
    store.delivery(
        id,
        &Delivery {
            destination_id: destination.into(),
            meeting_id: meeting_id.into(),
            status: "sent".into(),
            error: String::new(),
            receipt: json!({"source":"call","call_id":call}),
        },
    )
}

impl Tico {
    /// Claims the recording source for a live call.
    pub fn claim_call(&self, call: &str) -> Result<Value> {
        self.call(
            &format!("/api/v2/calls/{call}/recorder"),
            Some(&json!({})),
            &format!("recorder:call:{call}:claim:{}", uuid::Uuid::new_v4()),
        )
    }
    /// Claims the source, and gives it straight back when the call's meeting already holds transcript from an
    /// earlier source (Tico's `has_transcript`, else the meeting's own `seq`): a new recording numbers its lines from
    /// the start, which that meeting refuses, so this Recorder then records nothing. Answers the claim as Tico gave
    /// it, `source` false in that case, and `release_pending` when Tico did not take the source back after a few
    /// tries (the caller queues it).
    pub fn claim_fresh_call(&self, call: &str) -> Result<Value> {
        let mut reply = self.claim_call(call)?;
        if reply["source"] != true {
            return Ok(reply);
        }
        let written = match reply["has_transcript"].as_bool() {
            Some(written) => written,
            // Best effort: a meeting this Recorder cannot read yet is recorded as a fresh one.
            None => reply["meeting_id"].as_str().is_some_and(|meeting| {
                self.call(&format!("/api/v2/live-meetings/{meeting}"), None, "")
                    .is_ok_and(|view| view["seq"].as_i64().unwrap_or(0) > 0)
            }),
        };
        if written {
            reply["source"] = json!(false);
            let released = (0..3).any(|attempt| {
                if attempt > 0 {
                    std::thread::sleep(std::time::Duration::from_millis(300));
                }
                self.release_call(call).is_ok()
            });
            if !released {
                reply["release_pending"] = json!(true);
            }
        }
        Ok(reply)
    }
    /// Gives the source back. A call Tico no longer has (or a Tico without calls) has nothing to give back.
    pub fn release_call(&self, call: &str) -> Result<()> {
        match self.delete(
            &format!("/api/v2/calls/{call}/recorder"),
            &format!("recorder:call:{call}:release:{}", uuid::Uuid::new_v4()),
        ) {
            Err(error) if claim_outcome(Err(format!("{error:#}"))) == Claim::NoCalls => Ok(()),
            other => other.map(drop),
        }
    }
    /// Who this Recorder sees in the Meet window, for the call's live status while Google gives none.
    pub fn call_presence(&self, call: &str, names: &[String]) -> Result<Value> {
        let names: Vec<&String> = names.iter().take(100).collect();
        self.call(
            &format!("/api/v2/calls/{call}/presence"),
            Some(&json!({"names": names})),
            &format!("recorder:call:{call}:presence:{}", uuid::Uuid::new_v4()),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const CALL: &str = "6f1c1a52-6a4e-4c39-9d43-6a1f0e3c2b10";

    #[test]
    fn a_claim_answer_says_whether_this_recorder_records_the_call() {
        assert_eq!(
            claim_outcome(Ok(
                json!({"source":true,"meeting_id":"m1","title":" Pricing ","source_actor":"human:1"})
            )),
            Claim::Source {
                meeting_id: "m1".into(),
                title: "Pricing".into()
            }
        );
        assert_eq!(
            claim_outcome(Ok(
                json!({"source":false,"meeting_id":"m1","title":"Pricing","source_actor":"human:2"})
            )),
            Claim::NotSource
        );
        assert!(matches!(
            claim_outcome(Ok(json!({"source":true}))),
            Claim::Failed(_)
        ));
        // A Tico without Meet calls answers 404: nothing new, not an error.
        assert_eq!(
            claim_outcome(Err(format!("Tico request failed: {OLD_TICO}"))),
            Claim::NoCalls
        );
        assert_eq!(
            claim_outcome(Err(
                "Server returned HTTP 404 (not_found). Check the connection or retry.".into()
            )),
            Claim::NoCalls
        );
        assert!(matches!(
            claim_outcome(Err(
                "Server returned HTTP 409 (call_ended). Check the connection or retry.".into()
            )),
            Claim::Failed(_)
        ));
    }

    #[test]
    fn a_call_recording_is_marked_sent_once_and_learns_its_meeting() {
        let directory = tempfile::tempdir().unwrap();
        let store = Store::open(directory.path()).unwrap();
        let record = store.create("Call").unwrap();
        mark_sent(&store, &record.id, "acme", CALL, "").unwrap();
        mark_sent(&store, &record.id, "acme", CALL, "").unwrap();
        let deliveries = store.get(&record.id).unwrap().deliveries;
        assert_eq!(deliveries.len(), 1);
        assert_eq!(
            (
                deliveries[0].status.as_str(),
                deliveries[0].meeting_id.as_str()
            ),
            ("sent", "")
        );
        assert_eq!(deliveries[0].receipt["call_id"], CALL);
        mark_sent(&store, &record.id, "acme", CALL, "meeting-1").unwrap();
        assert_eq!(
            store.get(&record.id).unwrap().deliveries[0].meeting_id,
            "meeting-1"
        );
    }
}
