import './motion.css';

export { FLIGHT, MOTION, type DropletSize } from './config';
export { motionLevel, levelRank, useMotionLevel, useMotionRoot, setMotionChoice, type MotionLevel, type MotionChoice } from './level';
export { flight, onBurst, type FlightOpts, type FlightKind, type BurstReport } from './flight';
export { wash, pop, ripple } from './wash';
export { findTarget, waitForTarget, registerTargetResolver, type FindOpts, type VirtualResolver } from './target';
export { now, after, play, stop, onFrame, freeze, seek, thaw, isFrozen, freezeParam } from './clock';
export { clamp, eio, eout, kf, sample, f, KICK, SHAKE, BUMP, pulseOf } from './ease';
