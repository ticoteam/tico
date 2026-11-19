// The one call this window is in: one LiveKit room, held outside React so the press that starts it can open the
// microphone and unlock sound inside the press itself (platforms.md: Safari allows both only there). Nothing here
// joins or opens the microphone by itself: prime() runs from a person's press, and join() follows it.
// LiveKit loads on demand (it is most of a megabyte): the first press asks for it; the microphone is still asked for
// inside the press, and sound a browser held back waits for "Play audio".
import { useSyncExternalStore } from 'react';
import type * as LiveKitModule from 'livekit-client';
import type { DisconnectReason, LocalTrackPublication, Participant, RemoteParticipant, RemoteTrack, Room, TranscriptionSegment } from 'livekit-client';
import type { Call } from '@/api/schema';
import { ticoRoomQuery } from '@/conversation';
import { queryClient } from '@/data';
import { callKeys, clearStage, freshCall, joinCall, leaveCall } from './queries';

type LiveKit = typeof LiveKitModule;
let lk: LiveKit | null = null;
let loading: Promise<LiveKit> | null = null;
/** LiveKit, loaded once. A page that may start a call can call it early so the first press finds it ready. */
export const loadLiveKit = (): Promise<LiveKit> => (loading ??= import('livekit-client').then((m) => (lk = m)));

export type Phase = 'idle' | 'joining' | 'live' | 'reconnecting' | 'lost' | 'ended' | 'mic-refused' | 'no-mic';

export interface CallState {
  callId: string | null;
  phase: Phase;
  muted: boolean;
  /** My identity in the room (the actor id). */
  me: string | null;
  /** Everyone else in the room, in the order they came: people (their actor ids). */
  others: string[];
  /** The call's agents speak through one participant of their own (backend/call_media.py RECORDER_PREFIX); its level is theirs. */
  voice: string | null;
  speaking: string[];
  mutedIds: string[];
  /** The browser held sound back until a press: "Play audio". */
  audioBlocked: boolean;
  devices: { id: string; label: string }[];
  deviceId: string | null;
  /** The last words heard, when the server sends a transcript. */
  caption: { who: string; text: string } | null;
  startedAt: number | null;
  /** My screen is going out to the call. */
  sharing: boolean;
  /** Whose shared screen this window is receiving. */
  screen: string | null;
  /** The bar shows what is said (Captions). */
  captions: boolean;
  /** A call with agents (not Tico's own): its chat, `thread:<id>`, beside the page while the bar shows; null once closed. */
  chat: string | null;
}

const IDLE: CallState = {
  callId: null,
  phase: 'idle',
  muted: false,
  me: null,
  others: [],
  voice: null,
  speaking: [],
  mutedIds: [],
  audioBlocked: false,
  devices: [],
  deviceId: null,
  caption: null,
  startedAt: null,
  sharing: false,
  screen: null,
  captions: true,
  chat: null,
};
const MIC_KEY = 'tico-next:call-mic';
const VOICE_PREFIX = 'tico-recorder-';

let state: CallState = IDLE;
const subs = new Set<() => void>();
const set = (patch: Partial<CallState>) => {
  state = { ...state, ...patch };
  subs.forEach((f) => f());
};

let room: Room | null = null;
/** The room prime() made, once LiveKit is loaded. */
let ready: Promise<Room | null> | null = null;
let mic: Promise<MediaStream> | null = null;
let stream: MediaStream | null = null;
/** Bumped by every prime and leave, so a join that was overtaken stops at its next step. */
let gen = 0;
let sink: HTMLElement | null = null;
/** The shared screen coming in (one at a time: the stage shows the newest). */
let screenTrack: RemoteTrack | null = null;

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
  const g = gen;
  const make = (m: LiveKit): Room | null => {
    if (g !== gen) return null;
    const r = new m.Room({ adaptiveStream: false, dynacast: false });
    room = r;
    wire(r, m);
    void r.startAudio().catch(() => undefined);
    return r;
  };
  // in the press when LiveKit is here already; otherwise as soon as it arrives
  ready = lk ? Promise.resolve(make(lk)) : loadLiveKit().then(make, () => null);
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
  const m = mic;
  const r = ready ? await ready : null;
  if (g !== gen) return;
  if (!r || !m || !lk) {
    if (m) set({ callId, phase: 'lost' });
    return;
  }
  const { Track } = lk;
  set({ callId });
  try {
    stream = await m;
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
    set({ phase: 'live', me: r.localParticipant.identity, startedAt: Date.now(), audioBlocked: !r.canPlaybackAudio, chat: chatOf(j.call) });
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
  set({ phase: 'ended', speaking: [], caption: null, sharing: false, screen: null, chat: null });
  if (id && was) await leaveCall(id).catch(() => undefined);
}

/** Back to nothing: a page that showed an ended call is gone. */
export function forget(): void {
  if (isActive(state)) return;
  gen += 1;
  teardown();
  set(IDLE);
}

/** The phone's lip and the bar: the other of what it is now. */
export const toggleMute = (): Promise<void> => setMuted(!state.muted);

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

/** The agents' chat beside the page: a call with agents, unless it is Tico's own (Tico is the Dot). */
function chatOf(c: Call | undefined): string | null {
  if (!c?.bots.length || !c.thread_id) return null;
  return c.thread_id === queryClient.getQueryData(ticoRoomQuery.queryKey) ? null : `thread:${c.thread_id}`;
}

/** The browser can send a screen (a phone cannot; the desktop app says when it may not). */
export const canShare = (): boolean =>
  typeof navigator !== 'undefined' &&
  typeof navigator.mediaDevices?.getDisplayMedia === 'function' &&
  !(window as { ticoScreenCaptureUnavailable?: boolean }).ticoScreenCaptureUnavailable;

/** Start or stop sending my screen. False when the person said no in the browser's picker. */
export async function shareScreen(on: boolean): Promise<boolean> {
  const r = room;
  if (!r) return false;
  if (!on) {
    if (!state.sharing) return true;
    set({ sharing: false });
    await r.localParticipant.setScreenShareEnabled(false, { audio: false }).catch(() => undefined);
    return true;
  }
  set({ sharing: true });
  try {
    await r.localParticipant.setScreenShareEnabled(true, { audio: false });
    return true;
  } catch (e) {
    set({ sharing: false });
    if (e instanceof DOMException && e.name === 'NotAllowedError') return false;
    throw e;
  }
}

/** Draw the shared screen (mine while I share, else the one coming in) in a video element; gives back its undo. */
export function attachScreen(el: HTMLVideoElement): () => void {
  const t = state.sharing && lk ? room?.localParticipant.getTrackPublication(lk.Track.Source.ScreenShare)?.track : screenTrack;
  if (!t) return () => undefined;
  t.attach(el);
  return () => void t.detach(el);
}

export const setCaptions = (on: boolean): void => set({ captions: on });
export const hideChat = (): void => set({ chat: null });

async function devices(): Promise<void> {
  try {
    if (!lk) return;
    const list = await lk.Room.getLocalDevices('audioinput', false);
    set({ devices: list.filter((d) => d.deviceId).map((d, i) => ({ id: d.deviceId, label: d.label || `Microphone ${i + 1}` })) });
  } catch {
    // no list: the pick stays hidden
  }
}

function people(r: Room): void {
  const all = [...r.remoteParticipants.values()];
  const others = all.filter((p) => !p.identity.startsWith(VOICE_PREFIX));
  set({
    others: others.map((p) => p.identity),
    voice: all.find((p) => p.identity.startsWith(VOICE_PREFIX))?.identity ?? null,
    mutedIds: others.filter((p) => !p.isMicrophoneEnabled).map((p) => p.identity),
  });
}

function wire(r: Room, { DisconnectReason: Why, RoomEvent, Track }: LiveKit): void {
  const mine = () => room === r;
  const onPeople = () => mine() && people(r);
  // Someone came or went: the call's own view (who is in it, its agents) is read again; the room says so first.
  const onCome = () => {
    if (!mine()) return;
    people(r);
    if (state.callId) freshCall(state.callId);
  };
  r.on(RoomEvent.ParticipantConnected, onCome)
    .on(RoomEvent.ParticipantDisconnected, onCome)
    .on(RoomEvent.TrackMuted, onPeople)
    .on(RoomEvent.TrackUnmuted, onPeople)
    .on(RoomEvent.ActiveSpeakersChanged, (ps: Participant[]) => mine() && set({ speaking: ps.map((p) => p.identity) }))
    .on(RoomEvent.Reconnecting, () => mine() && set({ phase: 'reconnecting' }))
    .on(RoomEvent.Reconnected, () => mine() && set({ phase: 'live' }))
    .on(RoomEvent.Disconnected, (why?: DisconnectReason) => {
      if (!mine()) return;
      // The server closed the room: the call is over for everyone, not lost.
      const over = why === Why.ROOM_DELETED;
      set({ phase: over ? 'ended' : 'lost', speaking: [], caption: null });
      teardown();
      if (over && state.callId) freshCall(state.callId);
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
    .on(RoomEvent.TrackSubscribed, (t: RemoteTrack, _pub: unknown, p?: RemoteParticipant) => {
      if (t.kind === Track.Kind.Video && t.source === Track.Source.ScreenShare) {
        if (!mine()) return;
        screenTrack = t;
        set({ screen: p?.identity ?? '' });
        return;
      }
      if (t.kind !== Track.Kind.Audio) return;
      if (!sink) {
        sink = document.createElement('div');
        sink.hidden = true;
        document.body.append(sink);
      }
      sink.append(t.attach());
    })
    .on(RoomEvent.TrackUnsubscribed, (t: RemoteTrack) => {
      if (t === screenTrack) {
        screenTrack = null;
        if (mine()) set({ screen: null });
        return;
      }
      t.detach().forEach((el) => el.remove());
    })
    // The browser's own "Stop sharing": my screen leaves the stage too.
    .on(RoomEvent.LocalTrackUnpublished, (pub: LocalTrackPublication) => {
      if (!mine() || pub.source !== Track.Source.ScreenShare || !state.sharing) return;
      set({ sharing: false });
      const id = state.callId;
      const stage = id ? queryClient.getQueryData<Call>(callKeys.one(id))?.stage : null;
      if (id && stage?.kind === 'screen' && stage.by === state.me) void clearStage(id).catch(() => undefined);
    });
}

function teardown(): void {
  const r = room;
  room = null;
  ready = null;
  mic = null;
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  screenTrack = null;
  levels.clear();
  if (r) void r.disconnect();
}

// Closing the window hangs up; nothing asks first. Moving between pages does not (CallBar keeps the call).
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
