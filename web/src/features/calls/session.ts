// The one call this window is in: one LiveKit room, held outside React so the press that starts it can open the
// microphone and unlock sound inside the press itself (platforms.md: Safari allows both only there). Nothing here
// joins or opens the microphone by itself: prime() runs from a person's press, and join() follows it.
import { useSyncExternalStore } from 'react';
import { Room, RoomEvent, Track, type Participant, type RemoteTrack, type TranscriptionSegment } from 'livekit-client';
import { joinCall, leaveCall } from './queries';

export type Phase = 'idle' | 'joining' | 'live' | 'reconnecting' | 'lost' | 'ended' | 'mic-refused' | 'no-mic';

export interface CallState {
  callId: string | null;
  phase: Phase;
  muted: boolean;
  /** My identity in the room (the actor id). */
  me: string | null;
  /** Everyone else in the room, in the order they came. */
  others: string[];
  speaking: string[];
  mutedIds: string[];
  /** The browser held sound back until a press: "Tap to hear". */
  audioBlocked: boolean;
  devices: { id: string; label: string }[];
  deviceId: string | null;
  /** The last words heard, when the server sends a transcript. */
  caption: { who: string; text: string } | null;
  startedAt: number | null;
}

const IDLE: CallState = {
  callId: null,
  phase: 'idle',
  muted: false,
  me: null,
  others: [],
  speaking: [],
  mutedIds: [],
  audioBlocked: false,
  devices: [],
  deviceId: null,
  caption: null,
  startedAt: null,
};
const MIC_KEY = 'tico-next:call-mic';

let state: CallState = IDLE;
const subs = new Set<() => void>();
const set = (patch: Partial<CallState>) => {
  state = { ...state, ...patch };
  subs.forEach((f) => f());
};

let room: Room | null = null;
let mic: Promise<MediaStream> | null = null;
let stream: MediaStream | null = null;
/** Bumped by every prime and leave, so a join that was overtaken stops at its next step. */
let gen = 0;
let sink: HTMLElement | null = null;

export const callState = () => state;
export function useCall(): CallState {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => state,
    () => IDLE,
  );
}
/** In it, or on the way in: the states a page should not hang up from under. */
export const isActive = (s: CallState) => s.phase === 'joining' || s.phase === 'live' || s.phase === 'reconnecting';

const savedMic = (): string | null => {
  try {
    return localStorage.getItem(MIC_KEY);
  } catch {
    return null;
  }
};

/** First thing in the press: a room, the microphone asked for, sound unlocked. */
export function prime(callId: string | null): void {
  if (callId && callId === state.callId && isActive(state)) return;
  // Joining another call hangs up this one, and the server hears it.
  if (state.callId && (state.phase === 'live' || state.phase === 'reconnecting')) void leaveCall(state.callId).catch(() => undefined);
  teardown();
  gen += 1;
  const r = new Room({ adaptiveStream: false, dynacast: false });
  room = r;
  wire(r);
  void r.startAudio().catch(() => undefined);
  const deviceId = savedMic();
  const md = typeof navigator !== 'undefined' ? navigator.mediaDevices : undefined;
  mic = md?.getUserMedia ? md.getUserMedia({ audio: deviceId ? { deviceId } : true }) : Promise.reject(new DOMException('No microphone', 'NotFoundError'));
  mic.catch(() => undefined);
  set({ ...IDLE, callId, phase: 'joining', deviceId });
}

/** A call started from a press has its id now: the page it opens on knows it is this one. */
export function claim(callId: string): void {
  if (state.phase === 'joining' && !state.callId) set({ callId });
}

/** After prime(): the server's token, the room, my microphone in it. */
export async function join(callId: string): Promise<void> {
  const g = gen;
  const r = room;
  if (!r || !mic) return;
  set({ callId });
  try {
    stream = await mic;
  } catch (e) {
    if (g !== gen) return;
    const name = e instanceof DOMException ? e.name : '';
    set({ phase: name === 'NotFoundError' || name === 'OverconstrainedError' ? 'no-mic' : 'mic-refused' });
    return teardown();
  }
  try {
    const j = await joinCall(callId);
    if (g !== gen) return;
    if (!j.token || !j.url) throw new Error('no_token');
    await r.connect(j.url, j.token);
    if (g !== gen) return void r.disconnect();
    const track = stream.getAudioTracks()[0];
    if (track) await r.localParticipant.publishTrack(track, { source: Track.Source.Microphone });
    if (g !== gen) return;
    set({ phase: 'live', me: r.localParticipant.identity, startedAt: Date.now(), audioBlocked: !r.canPlaybackAudio });
    people(r);
    void devices();
  } catch {
    if (g !== gen) return;
    set({ phase: 'lost' });
    teardown();
  }
}

/** Hang up: out of the room, and the server told. */
export async function leave(): Promise<void> {
  const id = state.callId;
  const was = state.phase === 'live' || state.phase === 'reconnecting';
  gen += 1;
  teardown();
  set({ phase: 'ended', speaking: [], caption: null });
  if (id && was) await leaveCall(id).catch(() => undefined);
}

/** Back to nothing: a page that showed an ended call is gone. */
export function forget(): void {
  if (isActive(state)) return;
  gen += 1;
  teardown();
  set(IDLE);
}

export async function setMuted(on: boolean): Promise<void> {
  if (!room) return;
  set({ muted: on });
  try {
    await room.localParticipant.setMicrophoneEnabled(!on);
  } catch {
    set({ muted: !on });
  }
}

export async function pickDevice(id: string): Promise<void> {
  try {
    localStorage.setItem(MIC_KEY, id);
  } catch {
    // private window: the pick lasts this call
  }
  set({ deviceId: id });
  await room?.switchActiveDevice('audioinput', id).catch(() => undefined);
}

/** "Tap to hear": a press lets the browser play the call. */
export function hear(): void {
  void room?.startAudio().then(
    () => set({ audioBlocked: !room?.canPlaybackAudio }),
    () => undefined,
  );
}

const levels = new Map<string, () => number>();
/** A voice's level 0..1 now, for the Dot and the bars. The same function each time, so React keeps it. */
export function levelOf(identity: string): () => number {
  let f = levels.get(identity);
  if (!f) {
    f = () => {
      if (!room) return 0;
      const p = room.localParticipant.identity === identity ? room.localParticipant : room.getParticipantByIdentity(identity);
      return p?.audioLevel ?? 0;
    };
    levels.set(identity, f);
  }
  return f;
}

async function devices(): Promise<void> {
  try {
    const list = await Room.getLocalDevices('audioinput', false);
    set({ devices: list.filter((d) => d.deviceId).map((d, i) => ({ id: d.deviceId, label: d.label || `Microphone ${i + 1}` })) });
  } catch {
    // no list: the pick stays hidden
  }
}

function people(r: Room): void {
  const others = [...r.remoteParticipants.values()];
  set({
    others: others.map((p) => p.identity),
    mutedIds: others.filter((p) => !p.isMicrophoneEnabled).map((p) => p.identity),
  });
}

function wire(r: Room): void {
  const mine = () => room === r;
  const onPeople = () => mine() && people(r);
  r.on(RoomEvent.ParticipantConnected, onPeople)
    .on(RoomEvent.ParticipantDisconnected, onPeople)
    .on(RoomEvent.TrackMuted, onPeople)
    .on(RoomEvent.TrackUnmuted, onPeople)
    .on(RoomEvent.ActiveSpeakersChanged, (ps: Participant[]) => mine() && set({ speaking: ps.map((p) => p.identity) }))
    .on(RoomEvent.Reconnecting, () => mine() && set({ phase: 'reconnecting' }))
    .on(RoomEvent.Reconnected, () => mine() && set({ phase: 'live' }))
    .on(RoomEvent.Disconnected, () => {
      if (!mine()) return;
      set({ phase: 'lost', speaking: [] });
      teardown();
    })
    .on(RoomEvent.AudioPlaybackStatusChanged, () => mine() && set({ audioBlocked: !r.canPlaybackAudio }))
    .on(RoomEvent.MediaDevicesChanged, () => mine() && void devices())
    .on(RoomEvent.TranscriptionReceived, (segs: TranscriptionSegment[], p?: Participant) => {
      const text = segs
        .map((s) => s.text)
        .join(' ')
        .trim();
      if (mine() && text) set({ caption: { who: p?.identity ?? '', text } });
    })
    .on(RoomEvent.TrackSubscribed, (t: RemoteTrack) => {
      if (t.kind !== Track.Kind.Audio) return;
      if (!sink) {
        sink = document.createElement('div');
        sink.hidden = true;
        document.body.append(sink);
      }
      sink.append(t.attach());
    })
    .on(RoomEvent.TrackUnsubscribed, (t: RemoteTrack) => t.detach().forEach((el) => el.remove()));
}

function teardown(): void {
  const r = room;
  room = null;
  mic = null;
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  levels.clear();
  if (r) void r.disconnect();
}

// Closing or leaving the page hangs up; nothing asks first.
if (typeof window !== 'undefined')
  window.addEventListener('pagehide', () => {
    if (room) void leave();
  });

/** Tests: start clean. */
export function resetCall(): void {
  gen += 1;
  teardown();
  state = IDLE;
}
